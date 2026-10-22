"""Concurrency controller for inter-frame pipeline parallelism.

Provides ConcurrencyController class that manages slot-based admission control
for concurrent frame processing. Supports both non-blocking try_acquire (for
discard semantics) and async wait_acquire (for backpressure semantics). The
concurrency limit is dynamically configurable within the range [1, 10].
"""

import asyncio


class ConcurrencyController:
    """Controls inter-frame concurrency with configurable admission semantics.

    When a new frame arrives, the caller can either:
    - Use try_acquire() for non-blocking check (discard on full)
    - Use wait_acquire(timeout) to wait for a slot with backpressure

    After frame processing completes or is cancelled, release() frees the slot
    for subsequent frames and wakes any waiters.
    """

    def __init__(self, limit: int = 3) -> None:
        """Initialize the concurrency controller.

        Args:
            limit: Maximum number of frames allowed in-flight concurrently.
                Must be in [1, 10]. Defaults to 3.
        """
        self._limit: int = limit
        self._in_flight: int = 0
        self._slot_available: asyncio.Event = asyncio.Event()
        self._slot_available.set()  # Initially slots are available

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
        if self._in_flight >= self._limit:
            self._slot_available.clear()
        return True

    async def wait_acquire(self, timeout: float | None = None) -> bool:
        """Wait for a processing slot to become available.

        Blocks the caller until a slot is free or the timeout expires.
        Provides backpressure to the capture loop so that frame reads
        are naturally throttled to match processing capacity.

        Args:
            timeout: Maximum seconds to wait. None means wait indefinitely.
                If timeout expires, returns False (frame should be discarded).

        Returns:
            True if a slot was acquired, False if timed out.
        """
        try:
            await asyncio.wait_for(self._slot_available.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False

        # Slot is available, acquire it
        self._in_flight += 1
        if self._in_flight >= self._limit:
            self._slot_available.clear()
        return True

    def release(self) -> None:
        """Release a processing slot after frame completion or cancellation.

        Decrements the in-flight count, making capacity available for the
        next arriving frame. Signals any waiters that a slot is free.
        Does not decrement below zero.
        """
        if self._in_flight > 0:
            self._in_flight -= 1
        # Signal that a slot is now available
        if self._in_flight < self._limit:
            self._slot_available.set()

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
        # Update event state based on new limit
        if self._in_flight < self._limit:
            self._slot_available.set()
        else:
            self._slot_available.clear()
        return True
