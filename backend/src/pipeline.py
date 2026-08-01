"""Pipeline Orchestrator - wires all telemetry extraction components together.

Manages the full processing pipeline:
Frame Extractor → Engine Analyzer → OCR → Stage Assignment → Record Assembly → WebSocket broadcast.

Handles Start/Stop control commands and manages PipelineState including
status transitions, current template, and sequence counter.
"""

import asyncio
import base64
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

import cv2
import numpy as np

from src.concurrency_controller import ConcurrencyController
from src.engine_analyzer import EngineAnalyzerConfig, EngineAnalysisResult, analyze_engines
from src.enums import OCRFieldStatus, PipelineStatus, SeparationState
from src.frame_extractor import FrameExtractor, FrameExtractorConfig
from src.gpu_detector import GPUCapabilities
from src.models import ROIConfiguration
from src.ocr_engine import EasyOCREngine, OCRFieldResult, OCRResult
from src.parallel_config import FPSMeter, ParallelPipelineConfig
from src.record_assembler import RecordAssembler
from src.reorder_buffer import BufferedResult, BufferOverflowError, ReorderBuffer
from src.stage_assignment import StageAssigner
from src.template_registry import TemplateRegistry, TemplateNotFoundError


logger = logging.getLogger(__name__)


@dataclass
class PipelineState:
    """Tracks the current state of the extraction pipeline."""

    status: PipelineStatus = PipelineStatus.STOPPED
    source_url: str | None = None
    source_validated: bool = False
    skip_frames: int = 30
    current_sequence: int = 0
    separation_state: SeparationState = SeparationState.PRE_SEPARATION
    active_template_name: str = "starship_rois"
    gpu_capabilities: GPUCapabilities | None = None
    processing_fps: float = 0.0  # actual frames processed per second


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
        self._ocr_engine: EasyOCREngine | None = None  # Lazy init (expensive)
        self._stage_assigner = StageAssigner()
        self._record_assembler = RecordAssembler()
        self._engine_analyzer_config = EngineAnalyzerConfig()

        # Thread pool executor for CPU-bound work
        self._executor: ThreadPoolExecutor | None = None

        # Inter-frame concurrency control
        self._concurrency_controller: ConcurrencyController | None = None
        self._t_zero_detected: bool = False
        self._frame_sequence_counter: int = 0

        # Pipeline state
        self._state = PipelineState(gpu_capabilities=gpu_capabilities)

        # Reorder buffer for sequential broadcasting
        self._reorder_buffer: ReorderBuffer | None = None
        self._broadcast_sequence: int = 0  # monotonic broadcast counter
        self._fps_meter = FPSMeter()

        # Stale gap tracking: when a gap was first noticed at next_expected
        self._gap_detected_at: float | None = None

        # FPS tracking (legacy, kept for compatibility)
        self._last_frame_time: float = 0.0

        # Active ROI configuration (resolved from template on start)
        self._roi_config: ROIConfiguration | None = None

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

    def _ensure_ocr_engine(self) -> EasyOCREngine:
        """Lazily initialize the OCR engine (expensive neural net load).

        Returns:
            The initialized EasyOCREngine instance.
        """
        if self._ocr_engine is None:
            logger.info("Initializing EasyOCR engine (this may take a moment)...")
            self._ocr_engine = EasyOCREngine(self._gpu_capabilities)
            logger.info("EasyOCR engine initialized.")
        return self._ocr_engine

    async def _run_engine_analysis(self, frame: np.ndarray) -> EngineAnalysisResult:
        """Run engine analysis in the thread pool executor.

        Offloads the CPU-bound OpenCV Hough Circle Detection to the thread
        pool, with a configurable timeout to prevent stalls.

        Args:
            frame: BGR numpy array (1920x1080).

        Returns:
            EngineAnalysisResult with engine statuses and detection accuracy.
            Returns a default result with empty statuses on timeout.
        """
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    self._executor,
                    analyze_engines,
                    frame,
                    self._roi_config.engine_groups,
                    self._engine_analyzer_config,
                    self._gpu_capabilities,
                ),
                timeout=self._parallel_config.stage_timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "Engine analysis timed out after %.1f seconds",
                self._parallel_config.stage_timeout_seconds,
            )
            return EngineAnalysisResult(
                engine_statuses={},
                detection_accuracy=0.0,
                engine_group_bounding_boxes=[],
            )

    async def _run_ocr_extraction(self, frame: np.ndarray) -> OCRResult:
        """Run OCR text extraction in the thread pool executor.

        Offloads the CPU-bound EasyOCR/PyTorch inference to the thread
        pool, with a configurable timeout to prevent stalls.

        Args:
            frame: BGR numpy array (1920x1080).

        Returns:
            OCRResult with extracted text fields.
            Returns a default result with all fields UNAVAILABLE on timeout.
        """
        loop = asyncio.get_running_loop()
        ocr_engine = self._ensure_ocr_engine()

        # After stage separation, skip the stage_sep_text ROI
        text_regions = self._roi_config.text_regions
        if self._state.separation_state == SeparationState.POST_SEPARATION:
            text_regions = {
                k: v for k, v in text_regions.items() if k != "stage_sep_text"
            }

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    self._executor,
                    ocr_engine.extract_text,
                    frame,
                    text_regions,
                ),
                timeout=self._parallel_config.stage_timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "OCR extraction timed out after %.1f seconds",
                self._parallel_config.stage_timeout_seconds,
            )
            unavailable = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
            return OCRResult(
                time=unavailable,
                speed_l=unavailable,
                speed_l_unit=unavailable,
                speed_r=unavailable,
                speed_r_unit=unavailable,
                altitude_l=unavailable,
                altitude_l_unit=unavailable,
                altitude_r=unavailable,
                altitude_r_unit=unavailable,
                stage_l=unavailable,
                stage_r=unavailable,
                stage_sep_text=unavailable,
            )

    async def _run_jpeg_encoding(self, frame: np.ndarray) -> str:
        """Encode a frame as JPEG in the thread pool executor.

        Offloads the CPU-bound JPEG encoding and base64 conversion to
        the thread pool. No timeout is applied for JPEG encoding.

        Args:
            frame: BGR numpy array (1920x1080).

        Returns:
            Base64-encoded JPEG string.
        """
        loop = asyncio.get_running_loop()

        def encode_fn() -> str:
            _, jpeg_buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
            return base64.b64encode(jpeg_buf.tobytes()).decode('ascii')

        return await loop.run_in_executor(self._executor, encode_fn)

    async def _handle_stale_gap(self) -> None:
        """Check for and advance past stale gaps in the reorder buffer.

        If the next_expected sequence number has been pending (not available
        in the buffer) for longer than stale_gap_timeout_seconds, advances
        past the gap, logs a warning, and drains the buffer.

        Implements Requirement 4.5: advance past gaps stalled for >5 seconds.
        """
        if self._reorder_buffer is None:
            return

        # If next_expected is already in the buffer, there's no gap
        if self._reorder_buffer.next_expected in self._reorder_buffer._buffer:
            self._gap_detected_at = None
            return

        # If buffer is empty, nothing to do
        if self._reorder_buffer.size == 0:
            self._gap_detected_at = None
            return

        now = time.monotonic()

        # First time noticing a gap — record the timestamp
        if self._gap_detected_at is None:
            self._gap_detected_at = now
            return

        # Check if the gap has been pending for longer than the timeout
        elapsed = now - self._gap_detected_at
        if elapsed > self._parallel_config.stale_gap_timeout_seconds:
            gap_seq = self._reorder_buffer.next_expected
            logger.warning(
                "Stale gap detected: seq=%d pending for %.1f seconds. "
                "Advancing past gap.",
                gap_seq,
                elapsed,
            )
            self._reorder_buffer.advance_past_gap(gap_seq)
            self._gap_detected_at = None
            await self._drain_reorder_buffer()

    async def _handle_buffer_overflow(self) -> None:
        """Check for and handle buffer overflow conditions.

        Triggers overflow handling if:
        - buffer.size >= max_buffer_size (120), OR
        - buffer.size >= buffer_overflow_threshold * concurrency_limit

        When overflow is detected, repeatedly discards the oldest gap until
        buffer.size <= concurrency_limit, logging a warning for each discarded
        frame. Then drains the buffer.

        Implements Requirements 4.6 and 8.2.
        """
        if self._reorder_buffer is None:
            return

        concurrency_limit = (
            self._concurrency_controller.limit
            if self._concurrency_controller is not None
            else self._parallel_config.concurrency_limit
        )
        max_buffer_size = self._parallel_config.max_buffer_size
        overflow_threshold = self._parallel_config.buffer_overflow_threshold

        # Check overflow conditions
        is_overflow = (
            self._reorder_buffer.size >= max_buffer_size
            or self._reorder_buffer.size >= overflow_threshold * concurrency_limit
        )

        if not is_overflow:
            return

        logger.warning(
            "Buffer overflow detected: size=%d (max=%d, threshold=%.0f×%d=%d). "
            "Discarding oldest gaps.",
            self._reorder_buffer.size,
            max_buffer_size,
            overflow_threshold,
            concurrency_limit,
            int(overflow_threshold * concurrency_limit),
        )

        # Discard oldest gaps until buffer.size <= concurrency_limit
        while self._reorder_buffer.size > concurrency_limit:
            try:
                discarded_seq = self._reorder_buffer.discard_oldest_gap()
                logger.warning(
                    "Discarded stale gap at seq=%d to reduce buffer pressure "
                    "(buffer size now: %d).",
                    discarded_seq,
                    self._reorder_buffer.size,
                )
            except ValueError:
                # No more gaps to discard
                logger.warning(
                    "No more gaps to discard, buffer size: %d.",
                    self._reorder_buffer.size,
                )
                break

        await self._drain_reorder_buffer()

    async def _submit_to_reorder_buffer(
        self, seq: int, record: Any, frame_b64: str | None
    ) -> None:
        """Submit a processed result to the reorder buffer and drain.

        Creates a BufferedResult and inserts it into the reorder buffer.
        If the buffer is full, logs a warning. Then triggers a drain to
        broadcast any consecutively available results. Also checks for
        stale gaps and buffer overflow conditions.

        Args:
            seq: Frame sequence number.
            record: The assembled TelemetryRecord.
            frame_b64: Base64-encoded JPEG frame, or None if unavailable.
        """
        if self._reorder_buffer is None:
            logger.warning("Reorder buffer not initialized, skipping submit.")
            return

        # Check for stale gaps on each frame arrival (piggyback approach)
        await self._handle_stale_gap()

        buffered = BufferedResult(
            record=record,
            frame_b64=frame_b64,
            inserted_at=time.monotonic(),
        )

        try:
            self._reorder_buffer.insert(seq, buffered)
        except BufferOverflowError:
            logger.warning(
                "Reorder buffer overflow at seq=%d (size=%d). Discarding oldest gap.",
                seq,
                self._reorder_buffer.size,
            )
            self._reorder_buffer.discard_oldest_gap()
            # Retry insertion after freeing space
            try:
                self._reorder_buffer.insert(seq, buffered)
            except BufferOverflowError:
                logger.error(
                    "Reorder buffer still full after discard, dropping frame seq=%d.",
                    seq,
                )
                return

        # After inserting, check for buffer overflow
        await self._handle_buffer_overflow()

        # Drain consecutive results
        await self._drain_reorder_buffer()

        # After draining, check for stale gaps (may have a gap at next_expected)
        await self._handle_stale_gap()

    async def _drain_reorder_buffer(self) -> None:
        """Drain consecutive results from the reorder buffer and broadcast.

        For each drained result, assigns a monotonic broadcast sequence number,
        records the broadcast timestamp in FPSMeter, updates processing FPS,
        and broadcasts the telemetry record and frame preview via WebSocket.
        """
        if self._reorder_buffer is None:
            return

        drained = self._reorder_buffer.drain()

        for result in drained:
            # Assign monotonic broadcast sequence number
            self._broadcast_sequence += 1
            result.record.sequence_number = self._broadcast_sequence

            # Update pipeline state
            self._state.current_sequence = self._broadcast_sequence

            # Record broadcast timestamp and update FPS
            self._fps_meter.record_broadcast(time.monotonic())
            self._state.processing_fps = self._fps_meter.get_fps()

            # Broadcast telemetry record
            await self._broadcast({
                "type": "telemetry",
                "payload": result.record.model_dump(),
            })

            # Broadcast frame preview if available
            if result.frame_b64 is not None:
                try:
                    await self._broadcast({
                        "type": "frame",
                        "payload": {
                            "image": result.frame_b64,
                            "sequence": self._broadcast_sequence,
                            "processing_fps": self._state.processing_fps,
                        },
                    })
                except Exception as frame_err:
                    logger.warning(f"Failed to broadcast frame: {frame_err}")

    async def _process_frame_parallel(self, frame: np.ndarray, seq: int) -> None:
        """Process a frame with intra-frame parallelism using asyncio.gather.

        Runs engine analysis and OCR extraction concurrently. If one stage
        fails, uses a default result for the failed stage and continues with
        the successful result. If both fail, the frame is skipped.

        JPEG encoding is also run concurrently via the thread pool.

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number from the extractor.
        """
        if self._roi_config is None:
            logger.warning("No ROI configuration available, skipping frame.")
            return

        try:
            # Step 1: Run engine analysis and OCR extraction concurrently
            # Also start JPEG encoding concurrently
            engine_result_or_exc, ocr_result_or_exc, frame_b64 = await asyncio.gather(
                self._run_engine_analysis(frame),
                self._run_ocr_extraction(frame),
                self._run_jpeg_encoding(frame),
                return_exceptions=True,
            )

            # Step 2: Handle exception results from engine analysis
            if isinstance(engine_result_or_exc, BaseException):
                logger.error(
                    "Engine analysis failed for frame %d: %s",
                    seq,
                    engine_result_or_exc,
                    exc_info=engine_result_or_exc,
                )
                engine_result = EngineAnalysisResult(
                    engine_statuses={},
                    detection_accuracy=0.0,
                    engine_group_bounding_boxes=[],
                )
            else:
                engine_result = engine_result_or_exc

            # Step 3: Handle exception results from OCR extraction
            if isinstance(ocr_result_or_exc, BaseException):
                logger.error(
                    "OCR extraction failed for frame %d: %s",
                    seq,
                    ocr_result_or_exc,
                    exc_info=ocr_result_or_exc,
                )
                unavailable = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
                ocr_result = OCRResult(
                    time=unavailable,
                    speed_l=unavailable,
                    speed_l_unit=unavailable,
                    speed_r=unavailable,
                    speed_r_unit=unavailable,
                    altitude_l=unavailable,
                    altitude_l_unit=unavailable,
                    altitude_r=unavailable,
                    altitude_r_unit=unavailable,
                    stage_l=unavailable,
                    stage_r=unavailable,
                    stage_sep_text=unavailable,
                )
            else:
                ocr_result = ocr_result_or_exc

            # Step 4: If both stages failed, skip frame without crashing
            if isinstance(engine_result_or_exc, BaseException) and isinstance(
                ocr_result_or_exc, BaseException
            ):
                logger.error(
                    "Both engine analysis and OCR failed for frame %d, skipping frame.",
                    seq,
                )
                return

            # Step 5: Stage Assignment
            stage_result = self._stage_assigner.assign(ocr_result)

            # Step 6: Record Assembly
            ocr_engine = self._ensure_ocr_engine()
            record = self._record_assembler.assemble(
                engine_result=engine_result,
                ocr_result=ocr_result,
                stage_result=stage_result,
                engine_groups=self._roi_config.engine_groups,
                t_zero_found=ocr_engine.t_zero_detected,
                stage_sep_found=stage_result.separation_state == SeparationState.POST_SEPARATION,
            )

            # Step 7: Update separation state
            self._state.separation_state = stage_result.separation_state

            # Step 8: Handle frame_b64 exception (only pass str | None to buffer)
            actual_frame_b64: str | None = None
            if isinstance(frame_b64, BaseException):
                logger.warning(
                    "Failed to encode frame %d: %s", seq, frame_b64
                )
            else:
                actual_frame_b64 = frame_b64

            # Step 9: Submit to reorder buffer for ordered broadcast
            await self._submit_to_reorder_buffer(seq, record, actual_frame_b64)

        except Exception as e:
            logger.error(f"Error processing frame {seq}: {e}", exc_info=True)
            # Skip this frame, pipeline continues

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

        self._roi_config = template_result

        # Reset per-session state
        self._stage_assigner.reset()
        self._record_assembler.reset()
        self._state.source_url = source_url
        self._state.skip_frames = skip_frames
        self._state.current_sequence = 0
        self._state.separation_state = SeparationState.PRE_SEPARATION

        # Ensure OCR engine is loaded
        self._ensure_ocr_engine()

        # Create thread pool executor for CPU-bound work
        self._executor = ThreadPoolExecutor(
            max_workers=self._parallel_config.executor_max_workers
        )

        # Create concurrency controller for inter-frame parallelism
        self._concurrency_controller = ConcurrencyController(
            limit=self._parallel_config.concurrency_limit
        )

        # Reset frame sequence counter and T-0 state for new session
        self._frame_sequence_counter = 0
        self._t_zero_detected = False

        # Initialize reorder buffer for sequential broadcasting
        self._reorder_buffer = ReorderBuffer(max_size=self._parallel_config.max_buffer_size)
        self._broadcast_sequence = 0
        self._fps_meter = FPSMeter()
        self._gap_detected_at = None

        # Create frame extractor config and start
        config = FrameExtractorConfig(
            source_url=source_url,
            skip_frames=skip_frames,
        )

        self._frame_extractor.start(config)
        self._state.status = PipelineStatus.RUNNING

        # Broadcast status change
        await self._broadcast_status()

    def stop(self) -> None:
        """Stop the extraction pipeline.

        Stops frame capture, shuts down the thread pool executor,
        and resets per-session components.
        """
        if self._state.status == PipelineStatus.STOPPED:
            logger.info("Pipeline is already stopped.")
            return

        self._frame_extractor.stop()

        # Shutdown the thread pool executor
        if self._executor is not None:
            try:
                self._executor.shutdown(
                    wait=True,
                    cancel_futures=True,
                )
            except Exception as e:
                logger.error(f"Error shutting down executor: {e}")
            finally:
                self._executor = None

        self._stage_assigner.reset()
        self._record_assembler.reset()

        # Clear the reorder buffer
        if self._reorder_buffer is not None:
            self._reorder_buffer = None

        self._state.status = PipelineStatus.STOPPED
        self._state.current_sequence = 0
        self._state.separation_state = SeparationState.PRE_SEPARATION

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

    async def _on_frame(self, frame: np.ndarray, seq: int) -> None:
        """Dispatch a captured frame for processing based on T-0 state.

        Implements the frame dispatch logic:
        1. Assigns a Frame_Sequence_Number (starting at 1, incrementing by 1)
        2. Checks T-0 detection via the OCR engine's t_zero_detected property
        3. Pre-T-0: processes frame sequentially via _process_frame_sequential
        4. Post-T-0: uses ConcurrencyController for admission control
           - If slot acquired: spawns _process_frame_parallel as async task
           - If at capacity: discards frame and logs warning

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number from the extractor.
        """
        if self._roi_config is None:
            logger.warning("No ROI configuration available, skipping frame.")
            return

        # Assign frame sequence number (pre-increment: starts at 1)
        self._frame_sequence_counter += 1
        frame_seq = self._frame_sequence_counter

        # Check T-0 detection state from the OCR engine
        ocr_engine = self._ensure_ocr_engine()
        if not self._t_zero_detected and ocr_engine.t_zero_detected:
            self._t_zero_detected = True
            logger.info("T-0 detected, enabling parallel frame processing mode.")

        if not self._t_zero_detected:
            # Pre-T-0: sequential processing
            await self._process_frame_sequential(frame, frame_seq)
        else:
            # Post-T-0: attempt parallel dispatch with concurrency control
            if self._concurrency_controller is not None and self._concurrency_controller.try_acquire():
                # Spawn parallel processing as a fire-and-forget async task
                task = asyncio.create_task(
                    self._process_frame_parallel(frame, frame_seq)
                )

                # Add done callback to release the concurrency slot
                def _on_task_done(t: asyncio.Task) -> None:
                    if self._concurrency_controller is not None:
                        self._concurrency_controller.release()

                task.add_done_callback(_on_task_done)
            else:
                # At capacity: discard frame
                logger.warning(
                    "Frame %d discarded: concurrency limit reached (%d in-flight).",
                    frame_seq,
                    self._concurrency_controller.in_flight if self._concurrency_controller else 0,
                )

    async def _process_frame_sequential(self, frame: np.ndarray, seq: int) -> None:
        """Process a single frame sequentially (pre-T-0 mode).

        Uses the same intra-frame parallelism as _process_frame_parallel
        (engine analysis + OCR run concurrently via asyncio.gather dispatched
        to the thread pool) but is awaited inline — no inter-frame overlap.
        This ensures frames are processed one at a time in capture order
        before T-0 is detected.

        After processing, checks if T-0 was detected during OCR and sets
        _t_zero_detected to enable parallel mode for subsequent frames.

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number.
        """
        if self._roi_config is None:
            logger.warning("No ROI configuration available, skipping frame.")
            return

        try:
            # Step 1: Run engine analysis and OCR extraction concurrently
            # (intra-frame parallelism), plus JPEG encoding
            engine_result_or_exc, ocr_result_or_exc, frame_b64 = await asyncio.gather(
                self._run_engine_analysis(frame),
                self._run_ocr_extraction(frame),
                self._run_jpeg_encoding(frame),
                return_exceptions=True,
            )

            # Step 2: Handle exception results from engine analysis
            if isinstance(engine_result_or_exc, BaseException):
                logger.error(
                    "Engine analysis failed for frame %d: %s",
                    seq,
                    engine_result_or_exc,
                    exc_info=engine_result_or_exc,
                )
                engine_result = EngineAnalysisResult(
                    engine_statuses={},
                    detection_accuracy=0.0,
                    engine_group_bounding_boxes=[],
                )
            else:
                engine_result = engine_result_or_exc

            # Step 3: Handle exception results from OCR extraction
            if isinstance(ocr_result_or_exc, BaseException):
                logger.error(
                    "OCR extraction failed for frame %d: %s",
                    seq,
                    ocr_result_or_exc,
                    exc_info=ocr_result_or_exc,
                )
                unavailable = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
                ocr_result = OCRResult(
                    time=unavailable,
                    speed_l=unavailable,
                    speed_l_unit=unavailable,
                    speed_r=unavailable,
                    speed_r_unit=unavailable,
                    altitude_l=unavailable,
                    altitude_l_unit=unavailable,
                    altitude_r=unavailable,
                    altitude_r_unit=unavailable,
                    stage_l=unavailable,
                    stage_r=unavailable,
                    stage_sep_text=unavailable,
                )
            else:
                ocr_result = ocr_result_or_exc

            # Step 4: If both stages failed, skip frame without crashing
            if isinstance(engine_result_or_exc, BaseException) and isinstance(
                ocr_result_or_exc, BaseException
            ):
                logger.error(
                    "Both engine analysis and OCR failed for frame %d, skipping frame.",
                    seq,
                )
                return

            # Step 5: Check for T-0 detection after OCR completes
            ocr_engine = self._ensure_ocr_engine()
            if not self._t_zero_detected and ocr_engine.t_zero_detected:
                self._t_zero_detected = True
                logger.info(
                    "T-0 detected during sequential processing of frame %d, "
                    "enabling parallel frame processing mode.",
                    seq,
                )

            # Step 6: Stage Assignment
            stage_result = self._stage_assigner.assign(ocr_result)

            # Step 7: Record Assembly
            record = self._record_assembler.assemble(
                engine_result=engine_result,
                ocr_result=ocr_result,
                stage_result=stage_result,
                engine_groups=self._roi_config.engine_groups,
                t_zero_found=ocr_engine.t_zero_detected,
                stage_sep_found=stage_result.separation_state == SeparationState.POST_SEPARATION,
            )

            # Step 8: Update separation state
            self._state.separation_state = stage_result.separation_state

            # Step 9: Handle frame_b64 exception (only pass str | None to buffer)
            actual_frame_b64: str | None = None
            if isinstance(frame_b64, BaseException):
                logger.warning(
                    "Failed to encode frame %d: %s", seq, frame_b64
                )
            else:
                actual_frame_b64 = frame_b64

            # Step 10: Submit to reorder buffer for ordered broadcast
            await self._submit_to_reorder_buffer(seq, record, actual_frame_b64)

        except Exception as e:
            logger.error(f"Error processing frame {seq}: {e}", exc_info=True)
            # Skip this frame, pipeline continues

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
            current_sequence, and processing_fps for API and WebSocket responses.
        """
        return {
            "status": self._state.status.value,
            "gpu": {
                "available": self._gpu_capabilities.gpu_available,
                "device_name": self._gpu_capabilities.device_name,
            },
            "skip_frames": self._state.skip_frames,
            "current_sequence": self._state.current_sequence,
            "processing_fps": self._state.processing_fps,
        }
