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
from src.logging_context import frame_seq_var
from src.engine_analyzer import EngineAnalyzerConfig, EngineAnalysisResult, analyze_engines
from src.enums import PipelineStatus, SeparationState
from src.frame_extractor import FrameExtractor, FrameExtractorConfig
from src.gpu_detector import GPUCapabilities
from src.models import ROIConfiguration
from src.ocr_engine import EasyOCREngine, OCRResult
from src.parallel_config import FPSMeter, ParallelPipelineConfig
from src.record_assembler import RecordAssembler
from src.reorder_buffer import BufferedResult, BufferOverflowError, ReorderBuffer
from src.stage_assignment import SUPER_HEAVY, StageAssigner, StageAssignmentResult
from src.template_registry import TemplateRegistry, TemplateNotFoundError
from src.version import get_version


logger = logging.getLogger(__name__)

# Frame previews are encoded at this size: the dashboard shows a thumbnail,
# and a quarter of the pixels makes encoding, base64 and JSON ~4x cheaper.
_PREVIEW_SIZE = (960, 540)
_PREVIEW_JPEG_QUALITY = 60

# Previews are dropped (telemetry never is) once this many messages are
# waiting to be broadcast, so a slow client cannot build up a frame backlog.
_MAX_QUEUED_BROADCASTS = 8


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
    in_flight_frames: int = 0  # Current concurrent frame count
    t_zero_detected: bool = False  # Whether T-0 has been detected
    parallel_mode_active: bool = False  # Whether inter-frame parallelism is enabled
    frames_discarded: int = 0  # Count of frames discarded due to capacity
    buffer_size: int = 0  # Current reorder buffer occupancy


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

        # Thread pools for CPU-bound work: OCR gets its own pool sized to the
        # hardware so engine analysis and JPEG encoding never queue behind it
        self._executor: ThreadPoolExecutor | None = None
        self._ocr_executor: ThreadPoolExecutor | None = None

        # Ordered broadcast queue, drained by a single task so sending to
        # clients never holds up frame processing or its concurrency slot
        self._broadcast_queue: asyncio.Queue[dict[str, Any]] | None = None
        self._broadcaster_task: asyncio.Task | None = None

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

        # In-flight frame task tracking for graceful shutdown
        self._in_flight_tasks: set[asyncio.Task] = set()

        # Stale gap tracking: when a gap was first noticed at next_expected
        self._gap_detected_at: float | None = None

        # FPS tracking (legacy, kept for compatibility)
        self._last_frame_time: float = 0.0

        # FPS logging: track last logged value for 10% delta check
        self._last_logged_fps: float | None = None

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
        try:
            result, duration_ms, queue_ms = await asyncio.wait_for(
                self._run_timed(
                    self._executor,
                    analyze_engines,
                    frame,
                    self._roi_config.engine_groups,
                    self._engine_analyzer_config,
                    self._gpu_capabilities,
                ),
                timeout=self._parallel_config.stage_timeout_seconds,
            )
            seq = frame_seq_var.get()
            logger.debug(
                "Engine analysis completed",
                extra={"frame_seq": seq, "duration_ms": duration_ms, "queue_ms": queue_ms},
            )
            return result
        except asyncio.TimeoutError:
            seq = frame_seq_var.get()
            logger.warning(
                "Engine analysis timed out after %.1f seconds"
                " (stage=engine_analysis, frame_seq=%s, stage_timeout_seconds=%.1f)",
                self._parallel_config.stage_timeout_seconds,
                seq,
                self._parallel_config.stage_timeout_seconds,
            )
            return EngineAnalysisResult.empty()

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
        ocr_engine = self._ensure_ocr_engine()

        # After stage separation, skip the stage_sep_text ROI
        text_regions = self._roi_config.text_regions
        if self._state.separation_state == SeparationState.POST_SEPARATION:
            text_regions = {
                k: v for k, v in text_regions.items() if k != "stage_sep_text"
            }

        try:
            result, duration_ms, queue_ms = await asyncio.wait_for(
                self._run_timed(
                    self._ocr_executor,
                    ocr_engine.extract_text,
                    frame,
                    text_regions,
                ),
                timeout=self._parallel_config.stage_timeout_seconds,
            )
            seq = frame_seq_var.get()
            logger.debug(
                "OCR extraction completed",
                extra={"frame_seq": seq, "duration_ms": duration_ms, "queue_ms": queue_ms},
            )
            return result
        except asyncio.TimeoutError:
            seq = frame_seq_var.get()
            logger.warning(
                "OCR extraction timed out after %.1f seconds"
                " (stage=ocr_extraction, frame_seq=%s, stage_timeout_seconds=%.1f)",
                self._parallel_config.stage_timeout_seconds,
                seq,
                self._parallel_config.stage_timeout_seconds,
            )
            return OCRResult.unavailable()

    async def _run_jpeg_encoding(self, frame: np.ndarray) -> str:
        """Encode a downscaled preview of the frame as JPEG in the thread pool.

        Offloads the CPU-bound resize, JPEG encoding and base64 conversion to
        the thread pool. No timeout is applied for JPEG encoding.

        Args:
            frame: BGR numpy array (1920x1080).

        Returns:
            Base64-encoded JPEG string.
        """
        def encode_fn() -> str:
            preview = cv2.resize(frame, _PREVIEW_SIZE, interpolation=cv2.INTER_AREA)
            _, jpeg_buf = cv2.imencode(
                '.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, _PREVIEW_JPEG_QUALITY]
            )
            return base64.b64encode(jpeg_buf.tobytes()).decode('ascii')

        result, duration_ms, queue_ms = await self._run_timed(self._executor, encode_fn)
        logger.debug(
            "Preview encoding completed",
            extra={"duration_ms": duration_ms, "queue_ms": queue_ms},
        )
        return result

    @staticmethod
    async def _run_timed(
        executor: ThreadPoolExecutor | None, fn: Callable[..., Any], *args: Any
    ) -> tuple[Any, int, int]:
        """Run fn(*args) in the executor and time it from inside the worker.

        Returns:
            (result, compute time in ms, time spent waiting for a worker in ms).
            Timing inside the job keeps queue wait out of the stage duration.
        """
        loop = asyncio.get_running_loop()
        submitted = time.monotonic()

        def job() -> tuple[Any, float, float]:
            started = time.monotonic()
            return fn(*args), started, time.monotonic()

        result, started, finished = await loop.run_in_executor(executor, job)
        return result, int((finished - started) * 1000), int((started - submitted) * 1000)

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
            else self._parallel_config.resolve_concurrency_limit(
                self._parallel_config.resolve_ocr_workers(
                    self._gpu_capabilities.gpu_available
                )
            )
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
        self, seq: int, record: Any, frame_b64: str | None, ocr_result: OCRResult | None = None
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
            ocr_result=ocr_result,
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
            # Stage assignment is stateful, so it runs here, in video order
            if result.ocr_result is not None:
                self._apply_stage_assignment(result.record, result.ocr_result)

            # Assign monotonic broadcast sequence number
            self._broadcast_sequence += 1
            result.record.sequence_number = self._broadcast_sequence

            # Update pipeline state
            self._state.current_sequence = self._broadcast_sequence

            # Record broadcast timestamp and update FPS
            self._fps_meter.record_broadcast(time.monotonic())
            current_fps = self._fps_meter.get_fps()
            self._state.processing_fps = current_fps

            # FPS logging: first value at INFO, subsequent at INFO only when >10% delta
            if current_fps > 0:
                if self._last_logged_fps is None:
                    logger.info("Processing FPS: %.1f", current_fps, extra={"fps": current_fps})
                    self._last_logged_fps = current_fps
                else:
                    delta = abs(current_fps - self._last_logged_fps) / self._last_logged_fps
                    if delta > 0.10:
                        logger.info("Processing FPS: %.1f", current_fps, extra={"fps": current_fps})
                        self._last_logged_fps = current_fps

            # Queue the telemetry record (always) and the frame preview (only
            # while the queue is short) for the broadcaster task
            self._enqueue_broadcast({
                "type": "telemetry",
                "payload": result.record.model_dump(),
            })
            if result.frame_b64 is not None:
                self._enqueue_broadcast(
                    {
                        "type": "frame",
                        "payload": {
                            "image": result.frame_b64,
                            "sequence": self._broadcast_sequence,
                            "processing_fps": self._state.processing_fps,
                        },
                    },
                    droppable=True,
                )

        self._state.buffer_size = self._reorder_buffer.size

    def _enqueue_broadcast(self, message: dict[str, Any], droppable: bool = False) -> None:
        """Queue a message for the broadcaster task, preserving order.

        Args:
            message: The JSON-serialisable message to send to every client.
            droppable: Drop the message instead of queueing it when the queue
                already holds _MAX_QUEUED_BROADCASTS messages.
        """
        if self._broadcast_queue is None:
            return
        if droppable and self._broadcast_queue.qsize() >= _MAX_QUEUED_BROADCASTS:
            logger.debug("Broadcast queue full, dropping frame preview.")
            return
        self._broadcast_queue.put_nowait(message)

    async def _broadcast_worker(self) -> None:
        """Send queued messages to clients one at a time, in queue order."""
        assert self._broadcast_queue is not None
        queue = self._broadcast_queue
        while True:
            message = await queue.get()
            start = time.monotonic()
            try:
                await self._broadcast(message)
            except Exception as e:
                logger.warning("Failed to broadcast %s message: %s", message.get("type"), e)
            logger.debug(
                "Broadcast completed",
                extra={"duration_ms": int((time.monotonic() - start) * 1000)},
            )

    def _pending_stage_result(self) -> StageAssignmentResult:
        """Placeholder stage result for a record until _apply_stage_assignment runs."""
        state = self._stage_assigner.get_separation_state()
        return StageAssignmentResult(
            left_stage=SUPER_HEAVY, right_stage=SUPER_HEAVY, separation_state=state
        )

    def _apply_stage_assignment(self, record: Any, ocr_result: OCRResult) -> None:
        """Assign the record's stages from its OCR result, in video order.

        Frames finish processing out of order, but the assigner remembers the
        separation state and each side's vehicle from frame to frame, so it
        must see them in the order they appear in the video.
        """
        stage_result = self._stage_assigner.assign(ocr_result)
        post_sep = stage_result.separation_state == SeparationState.POST_SEPARATION
        record.stage_assignment_left = stage_result.left_stage
        record.stage_assignment_right = stage_result.right_stage
        record.separation_state = stage_result.separation_state.value
        record.stage_sep_found = post_sep
        self._state.separation_state = stage_result.separation_state

    def _skip_frame(self, seq: int) -> None:
        """Mark a frame that produced no result so later frames are not held up."""
        if self._reorder_buffer is not None:
            self._reorder_buffer.mark_skipped(seq)

    async def _process_frame_with_timeout(
        self, frame: np.ndarray, seq: int, frame_number: int, source_fps: float
    ) -> None:
        """Wrap frame processing with a configurable timeout.

        Calls _process_frame within asyncio.wait_for using
        frame_timeout_seconds (default 10s). On timeout, logs a warning,
        advances the reorder buffer's expected sequence past the timed-out
        frame, and drains any consecutive results that become available.

        The concurrency slot release and task tracking cleanup happen in the
        _on_task_done callback regardless of whether this method completes
        normally or via timeout.

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number from the extractor.
            frame_number: Absolute source frame index in the original stream.
            source_fps: Source video frame rate in frames per second.
        """
        try:
            await asyncio.wait_for(
                self._process_frame(
                    frame, seq, frame_number, source_fps, detect_t_zero=False
                ),
                timeout=self._parallel_config.frame_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Frame %d timed out after %.1f seconds (frame-level timeout). "
                "Advancing past frame. (frame_seq=%d, frame_timeout_seconds=%.1f)",
                seq,
                self._parallel_config.frame_timeout_seconds,
                seq,
                self._parallel_config.frame_timeout_seconds,
            )
            self._skip_frame(seq)
            await self._drain_reorder_buffer()

    def _unpack_stage_results(
        self,
        seq: int,
        engine_result_or_exc: EngineAnalysisResult | BaseException,
        ocr_result_or_exc: OCRResult | BaseException,
    ) -> tuple[EngineAnalysisResult, OCRResult] | None:
        """Replace a failed stage's exception with its default result.

        Logs each failed stage. When only one stage failed, the other's result
        is kept and the failed one falls back to an empty/UNAVAILABLE result.

        Args:
            seq: Frame sequence number (for logging).
            engine_result_or_exc: Engine analysis result or the exception it raised.
            ocr_result_or_exc: OCR extraction result or the exception it raised.

        Returns:
            (engine_result, ocr_result), or None when both stages failed and
            the frame should be skipped.
        """
        if isinstance(engine_result_or_exc, BaseException):
            logger.error(
                "Engine analysis failed for frame %d: %s",
                seq,
                engine_result_or_exc,
                exc_info=engine_result_or_exc,
            )
            engine_result = EngineAnalysisResult.empty()
        else:
            engine_result = engine_result_or_exc

        if isinstance(ocr_result_or_exc, BaseException):
            logger.error(
                "OCR extraction failed for frame %d: %s",
                seq,
                ocr_result_or_exc,
                exc_info=ocr_result_or_exc,
            )
            ocr_result = OCRResult.unavailable()
        else:
            ocr_result = ocr_result_or_exc

        if isinstance(engine_result_or_exc, BaseException) and isinstance(
            ocr_result_or_exc, BaseException
        ):
            logger.error(
                "Both engine analysis and OCR failed for frame %d, skipping frame.",
                seq,
            )
            return None
        return engine_result, ocr_result

    async def _process_frame(
        self,
        frame: np.ndarray,
        seq: int,
        frame_number: int,
        source_fps: float,
        *,
        detect_t_zero: bool,
    ) -> None:
        """Process a frame with intra-frame parallelism using asyncio.gather.

        Runs engine analysis, OCR extraction and JPEG encoding concurrently
        in the thread pools. If one stage fails, uses a default result for
        the failed stage and continues with the successful result. If both
        fail, the frame is skipped.

        Used both pre-T-0 (awaited inline from _on_frame, so frames are
        processed one at a time in capture order) and post-T-0 (spawned as
        a task via _process_frame_with_timeout, overlapping other frames).

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number from the extractor.
            frame_number: Absolute source frame index in the original stream.
            source_fps: Source video frame rate in frames per second.
            detect_t_zero: When True (pre-T-0 sequential mode), checks after
                OCR whether T-0 was detected and, if so, enables parallel
                mode for subsequent frames.
        """
        if self._roi_config is None:
            logger.warning("No ROI configuration available, skipping frame.")
            return

        frame_start = time.monotonic()
        try:
            # Step 1: Run engine analysis and OCR extraction concurrently
            # (intra-frame parallelism), plus JPEG encoding
            engine_result_or_exc, ocr_result_or_exc, frame_b64 = await asyncio.gather(
                self._run_engine_analysis(frame),
                self._run_ocr_extraction(frame),
                self._run_jpeg_encoding(frame),
                return_exceptions=True,
            )

            # Step 2: Fall back for a failed stage; skip the frame if both failed
            unpacked = self._unpack_stage_results(
                seq, engine_result_or_exc, ocr_result_or_exc
            )
            if unpacked is None:
                self._skip_frame(seq)
                await self._drain_reorder_buffer()
                return
            engine_result, ocr_result = unpacked

            # Step 3: Pre-T-0 only, check for T-0 detection after OCR completes
            ocr_engine = self._ensure_ocr_engine()
            if detect_t_zero and not self._t_zero_detected and ocr_engine.t_zero_detected:
                self._t_zero_detected = True
                self._state.t_zero_detected = True
                self._state.parallel_mode_active = True
                logger.info(
                    "T-0 detected during sequential processing of frame %d, "
                    "enabling parallel frame processing mode.",
                    seq,
                )

            # Step 4: Stage assignment runs when the result leaves the reorder
            # buffer (see _drain_reorder_buffer), in video order; the record
            # gets a placeholder until then
            stage_result = self._pending_stage_result()

            # Step 5: Record Assembly
            record = self._record_assembler.assemble(
                engine_result=engine_result,
                ocr_result=ocr_result,
                stage_result=stage_result,
                engine_groups=self._roi_config.engine_groups,
                frame_number=frame_number,
                source_fps=source_fps,
                t_zero_found=ocr_engine.t_zero_detected,
                stage_sep_found=stage_result.separation_state == SeparationState.POST_SEPARATION,
            )

            # Step 6: Handle frame_b64 exception (only pass str | None to buffer)
            actual_frame_b64: str | None = None
            if isinstance(frame_b64, BaseException):
                logger.warning(
                    "Failed to encode frame %d: %s", seq, frame_b64
                )
            else:
                actual_frame_b64 = frame_b64

            # Step 7: Submit to reorder buffer for ordered broadcast
            await self._submit_to_reorder_buffer(seq, record, actual_frame_b64, ocr_result)

            # Log total frame processing duration
            frame_end = time.monotonic()
            total_duration_ms = int((frame_end - frame_start) * 1000)
            logger.debug(
                "Frame processing completed",
                extra={"frame_seq": seq, "duration_ms": total_duration_ms},
            )

        except Exception as e:
            logger.error(f"Error processing frame {seq}: {e}", exc_info=True)
            # Skip this frame, pipeline continues
            self._skip_frame(seq)
            await self._drain_reorder_buffer()

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

        # Ensure OCR engine is loaded, with T-0 detection cleared from any
        # previous run so the time gate applies again
        self._ensure_ocr_engine().reset()

        # Create thread pools for CPU-bound work
        self._executor = ThreadPoolExecutor(
            max_workers=self._parallel_config.executor_max_workers,
            thread_name_prefix="pipeline",
        )
        ocr_workers = self._parallel_config.resolve_ocr_workers(
            self._gpu_capabilities.gpu_available
        )
        self._ocr_executor = ThreadPoolExecutor(
            max_workers=ocr_workers, thread_name_prefix="ocr"
        )
        logger.info("OCR thread pool: %d workers.", ocr_workers)

        # Start the ordered broadcaster
        self._broadcast_queue = asyncio.Queue()
        self._broadcaster_task = asyncio.create_task(self._broadcast_worker())

        # Create concurrency controller for inter-frame parallelism
        concurrency_limit = self._parallel_config.resolve_concurrency_limit(ocr_workers)
        self._concurrency_controller = ConcurrencyController(limit=concurrency_limit)
        logger.info("Concurrency limit: %d frames in flight.", concurrency_limit)

        # Reset frame sequence counter and T-0 state for new session
        self._frame_sequence_counter = 0
        self._t_zero_detected = False

        # Initialize reorder buffer for sequential broadcasting
        self._reorder_buffer = ReorderBuffer(max_size=self._parallel_config.max_buffer_size)
        self._broadcast_sequence = 0
        self._fps_meter = FPSMeter()
        self._last_logged_fps = None
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
        if self._reorder_buffer is not None:
            self._reorder_buffer = None

        # Step 6: Shut down the thread pools with a timeout each (Req 2.4)
        for executor in (self._executor, self._ocr_executor):
            if executor is not None:
                await self._shutdown_executor(executor)
        self._executor = None
        self._ocr_executor = None

        # Stop the broadcaster; anything still queued belongs to this run
        if self._broadcaster_task is not None:
            self._broadcaster_task.cancel()
            self._broadcaster_task = None
        self._broadcast_queue = None

        # Step 7: Reset components and parallel state
        self._stage_assigner.reset()
        self._record_assembler.reset()
        self._frame_sequence_counter = 0
        self._t_zero_detected = False
        self._concurrency_controller = None

        self._state.status = PipelineStatus.STOPPED
        self._state.current_sequence = 0
        self._state.separation_state = SeparationState.PRE_SEPARATION
        self._state.in_flight_frames = 0
        self._state.t_zero_detected = False
        self._state.parallel_mode_active = False
        self._state.frames_discarded = 0
        self._state.buffer_size = 0

    async def _shutdown_executor(self, executor: ThreadPoolExecutor) -> None:
        """Shut down a thread pool, forcing release after shutdown_timeout_seconds."""
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: executor.shutdown(wait=True, cancel_futures=True),
                ),
                timeout=self._parallel_config.shutdown_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Thread pool executor did not shut down within %.1f seconds. "
                "Forcing release.",
                self._parallel_config.shutdown_timeout_seconds,
            )
            # Force a non-blocking shutdown
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error shutting down executor: {e}")

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

    async def _on_frame(
        self, frame: np.ndarray, seq: int, frame_number: int, source_fps: float
    ) -> None:
        """Dispatch a captured frame for processing based on T-0 state.

        Implements the frame dispatch logic:
        1. Assigns a Frame_Sequence_Number (starting at 1, incrementing by 1)
        2. Checks T-0 detection via the OCR engine's t_zero_detected property
        3. Pre-T-0: awaits _process_frame inline (detect_t_zero=True)
        4. Post-T-0: waits for a concurrency slot (backpressure) with timeout
           - If slot acquired: spawns _process_frame (via
             _process_frame_with_timeout) as async task
           - If wait times out: discards frame and logs warning

        Args:
            frame: BGR numpy array (1920x1080).
            seq: Frame sequence number from the extractor.
            frame_number: Absolute source frame index in the original stream.
            source_fps: Source video frame rate in frames per second.
        """
        if self._roi_config is None:
            logger.warning("No ROI configuration available, skipping frame.")
            return

        # Assign frame sequence number (pre-increment: starts at 1)
        self._frame_sequence_counter += 1
        frame_seq = self._frame_sequence_counter

        # Set frame_seq context variable for structured logging
        frame_seq_var.set(frame_seq)

        # Check T-0 detection state from the OCR engine
        ocr_engine = self._ensure_ocr_engine()
        if not self._t_zero_detected and ocr_engine.t_zero_detected:
            self._t_zero_detected = True
            self._state.t_zero_detected = True
            self._state.parallel_mode_active = True
            logger.info("T-0 detected, enabling parallel frame processing mode.")

        if not self._t_zero_detected:
            # Pre-T-0: sequential processing
            await self._process_frame(
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
                        self._process_frame_with_timeout(
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
        return {
            "status": self._state.status.value,
            "version": get_version(),
            "gpu": {
                "available": self._gpu_capabilities.gpu_available,
                "device_name": self._gpu_capabilities.device_name,
            },
            "skip_frames": self._state.skip_frames,
            "current_sequence": self._state.current_sequence,
            "processing_fps": self._fps_meter.get_fps(),
            "in_flight_frames": (
                self._concurrency_controller.in_flight
                if self._concurrency_controller is not None
                else 0
            ),
            "parallel_mode_active": self._state.parallel_mode_active,
            "frames_discarded": self._state.frames_discarded,
        }
