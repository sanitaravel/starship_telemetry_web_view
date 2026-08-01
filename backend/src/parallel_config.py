"""Configuration and utility data models for the parallelized pipeline.

Defines ParallelPipelineConfig for tuning thread pool size, concurrency limits,
timeouts, and buffer thresholds. Also provides FPSMeter for sliding-window
FPS measurement based on broadcast timestamps.
"""

from collections import deque
from dataclasses import dataclass, field


@dataclass
class ParallelPipelineConfig:
    """Configuration for the parallelized pipeline.

    Controls thread pool sizing, concurrency limits, timeouts,
    and buffer management thresholds.
    """

    executor_max_workers: int = 8  # Thread pool size [1-8]
    concurrency_limit: int = 10  # Max concurrent frames [1-10]
    stage_timeout_seconds: float = 30.0  # Timeout for individual stages
    frame_timeout_seconds: float = 10.0  # Timeout for complete frame processing
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
        if not (1 <= self.concurrency_limit <= 10):
            errors.append("concurrency_limit must be between 1 and 10")
        if self.stage_timeout_seconds <= 0:
            errors.append("stage_timeout_seconds must be positive")
        if self.frame_timeout_seconds <= 0:
            errors.append("frame_timeout_seconds must be positive")
        return errors


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
