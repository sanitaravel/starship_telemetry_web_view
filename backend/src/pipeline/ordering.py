"""Reorder buffer coordination: frame results leave in video order.

Frames finish processing out of order. Each result goes into the reorder
buffer, and consecutive results are drained in capture order, given their
stage assignment and broadcast sequence number, and queued for broadcast.
Stale gaps and buffer overflow are handled here so a lost frame never stalls
the stream.
"""

import logging
import time
from typing import Any, Callable

from src.enums import SeparationState
from src.ocr.results import OCRResult
from src.pipeline.broadcast import Broadcaster
from src.pipeline.parallel_config import FPSMeter, ParallelPipelineConfig
from src.pipeline.reorder_buffer import BufferedResult, BufferOverflowError, ReorderBuffer
from src.pipeline.state import PipelineState
from src.stage_assignment import SUPER_HEAVY, StageAssigner, StageAssignmentResult


logger = logging.getLogger(__name__)


class ReorderCoordinator:
    """Owns the reorder buffer and the broadcast sequence and FPS it drives."""

    def __init__(
        self,
        parallel_config: ParallelPipelineConfig,
        state: PipelineState,
        stage_assigner: StageAssigner,
        broadcaster: Broadcaster,
        concurrency_limit: Callable[[], int],
    ) -> None:
        """Initialize the coordinator.

        Args:
            parallel_config: Buffer size, overflow and stale-gap tuning.
            state: Pipeline state, updated as results are broadcast.
            stage_assigner: Stateful stage assigner, fed results in video order.
            broadcaster: Queue that drained results are sent through.
            concurrency_limit: Returns the current inter-frame concurrency limit.
        """
        self._parallel_config = parallel_config
        self._state = state
        self._stage_assigner = stage_assigner
        self._broadcaster = broadcaster
        self._concurrency_limit = concurrency_limit

        # Reorder buffer for sequential broadcasting (created on start)
        self.buffer: ReorderBuffer | None = None
        self.broadcast_sequence: int = 0  # monotonic broadcast counter
        self.fps_meter = FPSMeter()

        # Stale gap tracking: when a gap was first noticed at next_expected
        self._gap_detected_at: float | None = None

        # FPS logging: track last logged value for 10% delta check
        self._last_logged_fps: float | None = None

    def reset(self) -> None:
        """Start a new session with an empty buffer and fresh counters."""
        self.buffer = ReorderBuffer(max_size=self._parallel_config.max_buffer_size)
        self.broadcast_sequence = 0
        self.fps_meter = FPSMeter()
        self._last_logged_fps = None
        self._gap_detected_at = None

    def clear(self) -> None:
        """Drop the buffer and anything still waiting in it."""
        self.buffer = None

    async def handle_stale_gap(self) -> None:
        """Check for and advance past stale gaps in the reorder buffer.

        If the next_expected sequence number has been pending (not available
        in the buffer) for longer than stale_gap_timeout_seconds, advances
        past the gap, logs a warning, and drains the buffer.

        Implements Requirement 4.5: advance past gaps stalled for >5 seconds.
        """
        if self.buffer is None:
            return

        # If next_expected is already in the buffer, there's no gap
        if self.buffer.next_expected in self.buffer._buffer:
            self._gap_detected_at = None
            return

        # If buffer is empty, nothing to do
        if self.buffer.size == 0:
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
            gap_seq = self.buffer.next_expected
            logger.warning(
                "Stale gap detected: seq=%d pending for %.1f seconds. "
                "Advancing past gap.",
                gap_seq,
                elapsed,
            )
            self.buffer.advance_past_gap(gap_seq)
            self._gap_detected_at = None
            await self.drain()

    async def handle_buffer_overflow(self) -> None:
        """Check for and handle buffer overflow conditions.

        Triggers overflow handling if:
        - buffer.size >= max_buffer_size (120), OR
        - buffer.size >= buffer_overflow_threshold * concurrency_limit

        When overflow is detected, repeatedly discards the oldest gap until
        buffer.size <= concurrency_limit, logging a warning for each discarded
        frame. Then drains the buffer.

        Implements Requirements 4.6 and 8.2.
        """
        if self.buffer is None:
            return

        concurrency_limit = self._concurrency_limit()
        max_buffer_size = self._parallel_config.max_buffer_size
        overflow_threshold = self._parallel_config.buffer_overflow_threshold

        # Check overflow conditions
        is_overflow = (
            self.buffer.size >= max_buffer_size
            or self.buffer.size >= overflow_threshold * concurrency_limit
        )

        if not is_overflow:
            return

        logger.warning(
            "Buffer overflow detected: size=%d (max=%d, threshold=%.0f×%d=%d). "
            "Discarding oldest gaps.",
            self.buffer.size,
            max_buffer_size,
            overflow_threshold,
            concurrency_limit,
            int(overflow_threshold * concurrency_limit),
        )

        # Discard oldest gaps until buffer.size <= concurrency_limit
        while self.buffer.size > concurrency_limit:
            try:
                discarded_seq = self.buffer.discard_oldest_gap()
                logger.warning(
                    "Discarded stale gap at seq=%d to reduce buffer pressure "
                    "(buffer size now: %d).",
                    discarded_seq,
                    self.buffer.size,
                )
            except ValueError:
                # No more gaps to discard
                logger.warning(
                    "No more gaps to discard, buffer size: %d.",
                    self.buffer.size,
                )
                break

        await self.drain()

    async def submit(
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
            ocr_result: The frame's OCR result, used for stage assignment
                when the record is drained.
        """
        if self.buffer is None:
            logger.warning("Reorder buffer not initialized, skipping submit.")
            return

        # Check for stale gaps on each frame arrival (piggyback approach)
        await self.handle_stale_gap()

        buffered = BufferedResult(
            record=record,
            frame_b64=frame_b64,
            inserted_at=time.monotonic(),
            ocr_result=ocr_result,
        )

        try:
            self.buffer.insert(seq, buffered)
        except BufferOverflowError:
            logger.warning(
                "Reorder buffer overflow at seq=%d (size=%d). Discarding oldest gap.",
                seq,
                self.buffer.size,
            )
            self.buffer.discard_oldest_gap()
            # Retry insertion after freeing space
            try:
                self.buffer.insert(seq, buffered)
            except BufferOverflowError:
                logger.error(
                    "Reorder buffer still full after discard, dropping frame seq=%d.",
                    seq,
                )
                return

        # After inserting, check for buffer overflow
        await self.handle_buffer_overflow()

        # Drain consecutive results
        await self.drain()

        # After draining, check for stale gaps (may have a gap at next_expected)
        await self.handle_stale_gap()

    async def drain(self) -> None:
        """Drain consecutive results from the reorder buffer and broadcast.

        For each drained result, assigns a monotonic broadcast sequence number,
        records the broadcast timestamp in FPSMeter, updates processing FPS,
        and broadcasts the telemetry record and frame preview via WebSocket.
        """
        if self.buffer is None:
            return

        drained = self.buffer.drain()

        for result in drained:
            # Stage assignment is stateful, so it runs here, in video order
            if result.ocr_result is not None:
                self.apply_stage_assignment(result.record, result.ocr_result)

            # Assign monotonic broadcast sequence number
            self.broadcast_sequence += 1
            result.record.sequence_number = self.broadcast_sequence

            # Update pipeline state
            self._state.current_sequence = self.broadcast_sequence

            # Record broadcast timestamp and update FPS
            self.fps_meter.record_broadcast(time.monotonic())
            current_fps = self.fps_meter.get_fps()
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
            self._broadcaster.enqueue({
                "type": "telemetry",
                "payload": result.record.model_dump(),
            })
            if result.frame_b64 is not None:
                self._broadcaster.enqueue(
                    {
                        "type": "frame",
                        "payload": {
                            "image": result.frame_b64,
                            "sequence": self.broadcast_sequence,
                            "processing_fps": self._state.processing_fps,
                        },
                    },
                    droppable=True,
                )

        self._state.buffer_size = self.buffer.size

    def pending_stage_result(self) -> StageAssignmentResult:
        """Placeholder stage result for a record until apply_stage_assignment runs."""
        state = self._stage_assigner.get_separation_state()
        return StageAssignmentResult(
            left_stage=SUPER_HEAVY, right_stage=SUPER_HEAVY, separation_state=state
        )

    def apply_stage_assignment(self, record: Any, ocr_result: OCRResult) -> None:
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

    def skip_frame(self, seq: int) -> None:
        """Mark a frame that produced no result so later frames are not held up."""
        if self.buffer is not None:
            self.buffer.mark_skipped(seq)
