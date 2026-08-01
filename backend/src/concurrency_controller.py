"""Concurrency controller for inter-frame pipeline parallelism.

Provides ConcurrencyController class that manages slot-based admission control
for concurrent frame processing. Uses non-blocking try_acquire/release semantics
with frame discard when at capacity. The concurrency limit is dynamically
configurable within the range [1, 10].
"""


class ConcurrencyController:
    """Controls inter-frame concurrency with frame discard semantics.

    When a new frame arrives, try_acquire() is called to check if a processing
    slot is available. If the number of in-flight frames is below the limit,
    the slot is acquired and the frame proceeds. If at capacity, the frame
    should be discarded by the caller.

    After frame processing completes or is cancelled, release() frees the slot
    for subsequent frames.
    """

    def __init__(self, limit: int = 3) -> None:
        """Initialize the concurrency controller.

        Args:
            limit: Maximum number of frames allowed in-flight concurrently.
                Must be in [1, 10]. Defaults to 3.
        """
        self._limit: int = limit
        self._in_flight: int = 0

    def try_acquire(self) -> bool:
        """Non-blocking attempt to acquire a processing slot.

        Returns True if a slot was acquired (in-flight count incremented),
        False if at capacity (frame should be discarded by the caller).

        Returns:
            True if the slot was acquired, False if at capacity.
        """
        if self._in_flight >= self._limit:
            return False
        self._in_flight += 1
        return True

    def release(self) -> None:
        """Release a processing slot after frame completion or cancellation.

        Decrements the in-flight count, making capacity available for the
        next arriving frame. Does not decrement below zero.
        """
        if self._in_flight > 0:
            self._in_flight -= 1

    @property
    def in_flight(self) -> int:
        """Number of frames currently being processed."""
        return self._in_flight

    @property
    def limit(self) -> int:
        """Current concurrency limit."""
        return self._limit

    def set_limit(self, new_limit: int) -> bool:
        """Update the concurrency limit.

        The new limit must be an integer in the range [1, 10]. If the value
        is outside this range, the configuration is rejected and the previous
        limit is retained.

        Args:
            new_limit: The desired new concurrency limit.

        Returns:
            True if the limit was updated, False if the value was rejected.
        """
        if not isinstance(new_limit, int) or not (1 <= new_limit <= 10):
            return False
        self._limit = new_limit
        return True
