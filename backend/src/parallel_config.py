"""Configuration and utility data models for the parallelized pipeline.

Defines ParallelPipelineConfig for tuning thread pool size, concurrency limits,
timeouts, and buffer thresholds. Also provides FPSMeter for sliding-window
FPS measurement based on broadcast timestamps.
"""

import os
from collections import deque
from dataclasses import dataclass, field


@dataclass
class ParallelPipelineConfig:
    """Configuration for the parallelized pipeline.

    Controls thread pool sizing, concurrency limits, timeouts,
    and buffer management thresholds.
    """

    executor_max_workers: int = 2  # Pool for engine analysis + JPEG [1-8]
    ocr_workers: int = 0  # OCR pool size [0-8]; 0 = auto (see resolve_ocr_workers)
    concurrency_limit: int = 6  # Max concurrent frames [1-10]
    stage_timeout_seconds: float = 30.0  # Timeout for individual stages
    # Timeout for complete frame processing. Must exceed stage_timeout_seconds,
    # otherwise the frame is abandoned while its OCR job keeps a worker busy.
    frame_timeout_seconds: float = 45.0
    max_buffer_size: int = 120  # Maximum reorder buffer entries
    stale_gap_timeout_seconds: float = 5.0  # Time before advancing past a gap
    shutdown_timeout_seconds: float = 10.0  # Max wait for executor shutdown
    buffer_overflow_threshold: float = 2.0  # Buffer size / concurrency_limit triggers trim

    def validate(self) -> list[str]:
        """Validate configuration values.

        Returns:
            List of error messages for out-of-range values.
            Empty list if all values are valid.
        """
        errors: list[str] = []
        if not (1 <= self.executor_max_workers <= 8):
            errors.append("executor_max_workers must be between 1 and 8")
        if not (0 <= self.ocr_workers <= 8):
            errors.append("ocr_workers must be between 0 (auto) and 8")
        if not (1 <= self.concurrency_limit <= 10):
            errors.append("concurrency_limit must be between 1 and 10")
        if self.stage_timeout_seconds <= 0:
            errors.append("stage_timeout_seconds must be positive")
        if self.frame_timeout_seconds <= 0:
            errors.append("frame_timeout_seconds must be positive")
        return errors

    def resolve_ocr_workers(self, gpu_available: bool) -> int:
        """Return the OCR pool size, picking one from the hardware when auto.

        On GPU all workers share one CUDA stream, so more than two only queue.
        On CPU each worker runs single-threaded torch (see
        ocr_engine.configure_torch_threads), so one worker per physical core
        (approximated as half the logical CPUs) keeps the cores busy without
        oversubscribing them.
        """
        if self.ocr_workers > 0:
            return self.ocr_workers
        if gpu_available:
            return 2
        return max(1, min(8, (os.cpu_count() or 2) // 2))


@dataclass
class FPSMeter:
    """Sliding-window FPS measurement based on broadcast timestamps.

    Tracks the most recent 10 broadcast event timestamps and computes
    the effective frames-per-second rate from that window.
    """

    _timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=10))

    def record_broadcast(self, timestamp: float) -> None:
        """Record a broadcast event timestamp.

        Args:
            timestamp: The monotonic timestamp of the broadcast event.
        """
        self._timestamps.append(timestamp)

    def get_fps(self) -> float:
        """Compute FPS from sliding window of last 10 broadcasts.

        Returns:
            The computed FPS rounded to 2 decimal places,
            or 0.0 if fewer than 2 broadcasts have been recorded.
        """
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        if elapsed <= 0:
            return 0.0
        return round((len(self._timestamps) - 1) / elapsed, 2)
