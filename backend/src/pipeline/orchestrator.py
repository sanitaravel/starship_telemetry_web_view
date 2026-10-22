"""Pipeline Orchestrator - wires all telemetry extraction components together.

Manages the full processing pipeline:
Frame Extractor → Engine Analyzer → OCR → Stage Assignment → Record Assembly → WebSocket broadcast.

Handles Start/Stop control commands and manages PipelineState including
status transitions, current template, and sequence counter. The per-frame
work, the reorder buffer and the broadcast queue live in collaborators
(FrameProcessor, ReorderCoordinator, Broadcaster) that this class owns.
"""

import asyncio
import logging
from typing import Any, Callable, Awaitable

import numpy as np

from src.enums import PipelineStatus, SeparationState
from src.frame_extractor import FrameExtractor, FrameExtractorConfig
from src.gpu_detector import GPUCapabilities
from src.logging_context import frame_seq_var
from src.ocr.engine import EasyOCREngine
from src.ocr.results import OCRResult
from src.pipeline.broadcast import Broadcaster
from src.pipeline.concurrency_controller import ConcurrencyController
from src.pipeline.frame_processor import FrameProcessor
from src.pipeline.ordering import ReorderCoordinator
from src.pipeline.parallel_config import ParallelPipelineConfig
from src.pipeline.stages import unpack_stage_results
from src.pipeline.state import PipelineState
from src.pipeline.status_payload import build_status_payload
from src.record_assembler import RecordAssembler
from src.stage_assignment import StageAssigner
from src.template_registry import TemplateRegistry, TemplateNotFoundError


logger = logging.getLogger(__name__)


def _forward(collaborator: str, name: str) -> property:
    """A read/write property for an attribute owned by one of the collaborators."""
    return property(
        lambda self: getattr(getattr(self, collaborator), name),
        lambda self, value: setattr(getattr(self, collaborator), name, value),
    )


class PipelineOrchestrator:
    """Orchestrates the telemetry extraction pipeline.

    Wires together Frame Extractor, Engine Analyzer, OCR Engine,
    Stage Assigner, and Record Assembler. Manages pipeline lifecycle
    (start/stop) and broadcasts results to connected WebSocket clients.
    """

    def __init__(
        self,
        gpu_capabilities: GPUCapabilities,
        template_registry: TemplateRegistry,
        broadcast: Callable[[dict[str, Any]], Awaitable[None]],
        parallel_config: ParallelPipelineConfig | None = None,
    ) -> None:
        """Initialize the pipeline orchestrator.

        Args:
            gpu_capabilities: Detected GPU capabilities for OCR acceleration.
            template_registry: Registry of ROI configuration templates.
            broadcast: Async callback to broadcast JSON messages to all
                connected WebSocket clients.
            parallel_config: Configuration for parallel pipeline execution.
                Uses default values if not provided.
        """
        self._gpu_capabilities = gpu_capabilities
        self._template_registry = template_registry
        self._broadcast = broadcast
        self._parallel_config = parallel_config or ParallelPipelineConfig()

        # Pipeline components
        self._frame_extractor = FrameExtractor()
        self._stage_assigner = StageAssigner()
        self._record_assembler = RecordAssembler()

        # Pipeline state
        self._state = PipelineState(gpu_capabilities=gpu_capabilities)

        # Ordered broadcast queue, drained by a single task so sending to
        # clients never holds up frame processing or its concurrency slot
        self._broadcaster = Broadcaster(broadcast)

        # Reorder buffer for sequential broadcasting
        self._ordering = ReorderCoordinator(
            self._parallel_config,
            self._state,
            self._stage_assigner,
            self._broadcaster,
            self._concurrency_limit,
        )

        # Per-frame work: OCR engine, ROI config, thread pools, T-0 flag
        self._frames = FrameProcessor(
            gpu_capabilities,
            self._parallel_config,
            self._state,
            self._record_assembler,
            self._ordering,
        )

        # Inter-frame concurrency control
        self._concurrency_controller: ConcurrencyController | None = None
        self._frame_sequence_counter: int = 0

        # In-flight frame task tracking for graceful shutdown
        self._in_flight_tasks: set[asyncio.Task] = set()

        # FPS tracking (legacy, kept for compatibility)
        self._last_frame_time: float = 0.0

        # Register callbacks on frame extractor
        self._frame_extractor.on_frame(self._on_frame)
        self._frame_extractor.on_status_change(self._on_status_change)

    @property
    def state(self) -> PipelineState:
        """Return the current pipeline state."""
        return self._state

    @property
    def frame_extractor(self) -> FrameExtractor:
        """Return the frame extractor instance (for URL validation)."""
        return self._frame_extractor

    # --- Collaborator state, reachable from the orchestrator (tests and tools) ---

    _reorder_buffer = _forward("_ordering", "buffer")
    _broadcast_queue = _forward("_broadcaster", "queue")
    _roi_config = _forward("_frames", "roi_config")
    _ocr_engine = _forward("_frames", "ocr_engine")
    _t_zero_detected = _forward("_frames", "t_zero_detected")

    def _ensure_ocr_engine(self) -> EasyOCREngine:
        """Lazily initialize the OCR engine (expensive neural net load)."""
        return self._frames.ensure_ocr_engine()

    _unpack_stage_results = staticmethod(unpack_stage_results)

    async def _process_frame(
        self, frame: np.ndarray, seq: int, frame_number: int, source_fps: float,
        *, detect_t_zero: bool,
    ) -> None:
        """Process one frame; see FrameProcessor.process_frame."""
        await self._frames.process_frame(
            frame, seq, frame_number, source_fps, detect_t_zero=detect_t_zero
        )

    async def _process_frame_with_timeout(
        self, frame: np.ndarray, seq: int, frame_number: int, source_fps: float
    ) -> None:
        """Process one frame under the frame timeout; see FrameProcessor."""
        await self._frames.process_frame_with_timeout(frame, seq, frame_number, source_fps)

    async def _submit_to_reorder_buffer(
        self, seq: int, record: Any, frame_b64: str | None, ocr_result: OCRResult | None = None
    ) -> None:
        """Submit a processed result for ordered broadcast; see ReorderCoordinator."""
        await self._ordering.submit(seq, record, frame_b64, ocr_result)

    def _concurrency_limit(self) -> int:
        """The current concurrency limit, or the configured one before start."""
        return (
            self._concurrency_controller.limit
            if self._concurrency_controller is not None
            else self._parallel_config.resolve_concurrency_limit(
                self._parallel_config.resolve_ocr_workers(
                    self._gpu_capabilities.gpu_available
                )
            )
        )

    # --- Lifecycle ---

    async def start(self, source_url: str, skip_frames: int = 30) -> None:
        """Start the extraction pipeline.

        Resolves the active template, resets per-session state, creates
        the frame extractor config, and starts frame capture.

        Args:
            source_url: Video source URL to capture frames from.
            skip_frames: Process every Nth frame from the stream.
        """
        if self._state.status == PipelineStatus.RUNNING:
            logger.warning("Pipeline is already running, ignoring start request.")
            return

        # Resolve the active ROI template
        template_result = self._template_registry.get(self._state.active_template_name)
        if isinstance(template_result, TemplateNotFoundError):
            # Fallback to default
            template_result = self._template_registry.get_default()
            if isinstance(template_result, TemplateNotFoundError):
                logger.error(
                    f"No ROI template available: {template_result.template_name}. "
                    f"Available: {template_result.available_templates}"
                )
                await self._broadcast({
                    "type": "error",
                    "payload": {
                        "message": "No ROI template loaded. Cannot start pipeline.",
                    },
                })
                return

        self._frames.roi_config = template_result

        # Reset per-session state
        self._stage_assigner.reset()
        self._record_assembler.reset()
        self._state.source_url = source_url
        self._state.skip_frames = skip_frames
        self._state.current_sequence = 0
        self._state.separation_state = SeparationState.PRE_SEPARATION

        # Ensure OCR engine is loaded, with T-0 detection cleared from any
        # previous run so the time gate applies again
        self._frames.ensure_ocr_engine().reset()

        # Create thread pools for CPU-bound work
        ocr_workers = self._frames.open_pools()

        # Start the ordered broadcaster
        self._broadcaster.start()

        # Create concurrency controller for inter-frame parallelism
        concurrency_limit = self._parallel_config.resolve_concurrency_limit(ocr_workers)
        self._concurrency_controller = ConcurrencyController(limit=concurrency_limit)
        logger.info("Concurrency limit: %d frames in flight.", concurrency_limit)

        # Reset frame sequence counter and T-0 state for new session
        self._frame_sequence_counter = 0
        self._frames.t_zero_detected = False

        # Initialize reorder buffer for sequential broadcasting
        self._ordering.reset()

        # Create frame extractor config and start
        config = FrameExtractorConfig(
            source_url=source_url,
            skip_frames=skip_frames,
        )

        self._frame_extractor.start(config)
        self._state.status = PipelineStatus.RUNNING

        # Broadcast status change
        await self._broadcast_status()

    async def stop(self) -> None:
        """Stop the extraction pipeline with graceful shutdown.

        Implements Requirements 2.4, 8.3, 8.4:
        1. Stops frame capture (no new frames dispatched)
        2. Cancels all in-flight frame processing tasks
        3. Waits up to 5 seconds for cancelled tasks to terminate
        4. If tasks not terminated in 5 seconds, abandons them and logs error
           with the count of un-terminated tasks
        5. Shuts down the thread pool executor with a 10-second timeout
        6. Clears reorder buffer and resets parallel state
        """
        if self._state.status == PipelineStatus.STOPPED:
            logger.info("Pipeline is already stopped.")
            return

        # Step 1: Stop frame extractor (prevents new frames from arriving)
        self._frame_extractor.stop()

        # Step 2: Cancel all in-flight frame processing tasks
        tasks_to_cancel = set(self._in_flight_tasks)
        if tasks_to_cancel:
            logger.info(
                "Cancelling %d in-flight frame tasks for graceful shutdown.",
                len(tasks_to_cancel),
            )
            for task in tasks_to_cancel:
                task.cancel()

            # Step 3: Wait up to 5 seconds for tasks to terminate
            done, pending = await asyncio.wait(
                tasks_to_cancel, timeout=5.0
            )

            # Step 4: If tasks still pending after 5 seconds, abandon and log error
            if pending:
                logger.error(
                    "Graceful shutdown: %d in-flight tasks did not terminate "
                    "within 5 seconds. Abandoning remaining tasks.",
                    len(pending),
                )

        # Clear the in-flight task tracking set
        self._in_flight_tasks.clear()

        # Step 5: Clear the reorder buffer (Req 8.3)
        self._ordering.clear()

        # Step 6: Shut down the thread pools with a timeout each (Req 2.4)
        await self._frames.close_pools()

        # Stop the broadcaster; anything still queued belongs to this run
        self._broadcaster.stop()

        # Step 7: Reset components and parallel state
        self._stage_assigner.reset()
        self._record_assembler.reset()
        self._frame_sequence_counter = 0
        self._frames.t_zero_detected = False
        self._concurrency_controller = None

        self._state.status = PipelineStatus.STOPPED
        self._state.current_sequence = 0
        self._state.separation_state = SeparationState.PRE_SEPARATION
        self._state.in_flight_frames = 0
        self._state.t_zero_detected = False
        self._state.parallel_mode_active = False
        self._state.frames_discarded = 0
        self._state.buffer_size = 0

    def set_skip_frames(self, skip_frames: int) -> None:
        """Update the frame skip count.

        Updates both the pipeline state and the frame extractor's active config.
        Takes effect on the next frame read cycle.

        Args:
            skip_frames: Process every Nth frame (minimum 1).
        """
        skip_frames = max(1, min(skip_frames, 300))
        self._state.skip_frames = skip_frames
        if self._frame_extractor._config is not None:
            self._frame_extractor._config.skip_frames = skip_frames
        logger.info(f"Frame skip set to every {skip_frames} frames")

    # --- Frame dispatch ---

    async def _on_frame(
        self, frame: np.ndarray, seq: int, frame_number: int, source_fps: float
    ) -> None:
        """Dispatch a captured frame for processing based on T-0 state.

        Implements the frame dispatch logic:
        1. Assigns a Frame_Sequence_Number (starting at 1, incrementing by 1)
        2. Checks T-0 detection via the OCR engine's t_zero_detected property
        3. Pre-T-0: awaits process_frame inline (detect_t_zero=True)
        4. Post-T-0: waits for a concurrency slot (backpressure) with timeout
           - If slot acquired: spawns process_frame (via
             process_frame_with_timeout) as async task
           - If wait times out: discards frame and logs warning

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number from the extractor.
            frame_number: Absolute source frame index in the original stream.
            source_fps: Source video frame rate in frames per second.
        """
        if self._frames.roi_config is None:
            logger.warning("No ROI configuration available, skipping frame.")
            return

        # Assign frame sequence number (pre-increment: starts at 1)
        self._frame_sequence_counter += 1
        frame_seq = self._frame_sequence_counter

        # Set frame_seq context variable for structured logging
        frame_seq_var.set(frame_seq)

        # Check T-0 detection state from the OCR engine
        ocr_engine = self._frames.ensure_ocr_engine()
        if not self._frames.t_zero_detected and ocr_engine.t_zero_detected:
            self._frames.t_zero_detected = True
            self._state.t_zero_detected = True
            self._state.parallel_mode_active = True
            logger.info("T-0 detected, enabling parallel frame processing mode.")

        if not self._frames.t_zero_detected:
            # Pre-T-0: sequential processing
            await self._frames.process_frame(
                frame, frame_seq, frame_number, source_fps, detect_t_zero=True
            )
        else:
            # Post-T-0: wait for a concurrency slot with backpressure.
            # This pauses the capture loop until processing capacity is available,
            # preventing frame waste on sources faster than real-time (local files,
            # local HLS streams). Timeout prevents indefinite blocking if processing
            # is permanently stuck.
            if self._concurrency_controller is not None:
                acquired = await self._concurrency_controller.wait_acquire(
                    timeout=self._parallel_config.frame_timeout_seconds
                )
                if acquired:
                    # Spawn parallel processing as a fire-and-forget async task
                    task = asyncio.create_task(
                        self._frames.process_frame_with_timeout(
                            frame, frame_seq, frame_number, source_fps
                        )
                    )

                    # Track the task for graceful shutdown
                    self._in_flight_tasks.add(task)

                    # Add done callback to release the concurrency slot and remove from tracking
                    def _on_task_done(t: asyncio.Task) -> None:
                        self._in_flight_tasks.discard(t)
                        if self._concurrency_controller is not None:
                            self._concurrency_controller.release()

                    task.add_done_callback(_on_task_done)
                else:
                    # Timed out waiting for a slot: discard frame
                    self._state.frames_discarded += 1
                    logger.warning(
                        "Frame %d discarded: timed out waiting for concurrency slot "
                        "(%d in-flight, limit=%d).",
                        frame_seq,
                        self._concurrency_controller.in_flight,
                        self._concurrency_controller.limit,
                    )

    # --- Status ---

    async def _on_status_change(self, new_status: PipelineStatus) -> None:
        """Handle frame extractor status changes.

        Updates internal state and broadcasts the new status to clients.

        Args:
            new_status: The new pipeline status from the frame extractor.
        """
        self._state.status = new_status
        await self._broadcast_status()

    async def _broadcast_status(self) -> None:
        """Broadcast the current pipeline status to all connected clients."""
        await self._broadcast({
            "type": "status",
            "payload": self.build_status_payload(),
        })

    def build_status_payload(self) -> dict[str, Any]:
        """Build the pipeline status payload dictionary.

        Returns:
            Dictionary with status, gpu info, skip_frames,
            current_sequence, processing_fps, in_flight_frames,
            parallel_mode_active, frames_discarded and the backend
            version for API and WebSocket responses.
        """
        return build_status_payload(
            self._state,
            self._gpu_capabilities,
            self._ordering.fps_meter.get_fps(),
            (
                self._concurrency_controller.in_flight
                if self._concurrency_controller is not None
                else 0
            ),
        )
