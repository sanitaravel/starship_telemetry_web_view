"""Reorder buffer for sequential broadcasting of out-of-order frame results.

Provides BufferedResult dataclass for storing completed frame results and
ReorderBuffer class for managing insertion, sequential draining, gap handling,
and overflow management. Results are drained in strict sequence number order
regardless of the order in which processing completes.
"""

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class BufferedResult:
    """A frame result waiting in the reorder buffer.

    Attributes:
        record: The telemetry record produced by frame processing.
        frame_b64: Base64-encoded JPEG frame, or None if encoding was skipped.
        inserted_at: Monotonic timestamp when the result was buffered.
    """

    record: Any
    frame_b64: str | None
    inserted_at: float


class ReorderBuffer:
    """Manages out-of-order result buffering and sequential draining.

    Completed frame results are inserted keyed by their sequence number.
    The drain() method returns all consecutively available results starting
    from the next expected sequence number, advancing the pointer as it goes.

    Gap handling allows the buffer to skip stalled frames (advance_past_gap)
    and free space when the buffer overflows (discard_oldest_gap).
    """

    def __init__(self, max_size: int = 120) -> None:
        """Initialize the reorder buffer.

        Args:
            max_size: Maximum number of buffered results before overflow
                handling is required. Defaults to 120.
        """
        self._buffer: dict[int, BufferedResult] = {}
        self._next_expected: int = 1
        self._max_size: int = max_size

    def insert(self, seq: int, result: BufferedResult) -> None:
        """Insert a completed result into the buffer.

        Args:
            seq: The frame sequence number for this result.
            result: The buffered result to store.

        Raises:
            BufferOverflowError: If the buffer is at maximum capacity.
        """
        if len(self._buffer) >= self._max_size:
            raise BufferOverflowError(
                f"Reorder buffer is full ({self._max_size} entries). "
                f"Cannot insert seq={seq}."
            )
        self._buffer[seq] = result

    def drain(self) -> list[BufferedResult]:
        """Return all consecutively available results starting from next_expected.

        Removes drained results from the buffer and advances the next_expected
        pointer past the last drained sequence number.

        Returns:
            List of BufferedResult instances in sequence order. May be empty
            if the next expected sequence number is not yet available.
        """
        results: list[BufferedResult] = []
        while self._next_expected in self._buffer:
            results.append(self._buffer.pop(self._next_expected))
            self._next_expected += 1
        return results

    def advance_past_gap(self, gap_seq: int) -> None:
        """Skip a stalled frame, advancing next_expected past it.

        If gap_seq is present in the buffer it is removed. The next_expected
        pointer is set to gap_seq + 1.

        Args:
            gap_seq: The sequence number of the stalled frame to skip.
        """
        self._buffer.pop(gap_seq, None)
        self._next_expected = gap_seq + 1

    def discard_oldest_gap(self) -> int:
        """Remove the oldest pending gap to free buffer space.

        Finds the smallest sequence number between next_expected and the
        minimum buffered sequence number that is NOT in the buffer (i.e.,
        a gap). Advances next_expected past that gap.

        Returns:
            The sequence number of the discarded gap.

        Raises:
            ValueError: If there is no gap to discard (buffer is empty or
                next_expected is already at the first buffered entry).
        """
        if not self._buffer:
            raise ValueError("Cannot discard gap from empty buffer.")

        # Find the smallest gap between next_expected and the buffer entries.
        # A gap is a sequence number that is expected but not present.
        seq = self._next_expected
        while seq in self._buffer:
            seq += 1

        # If seq is beyond all buffered entries, there is no gap to skip
        # (all entries from next_expected onward are consecutive).
        # In that case seq is the first missing one after the run — advance to it.
        self._next_expected = seq + 1
        return seq

    @property
    def size(self) -> int:
        """Current number of buffered results."""
        return len(self._buffer)

    @property
    def next_expected(self) -> int:
        """The next sequence number expected for broadcast."""
        return self._next_expected


class BufferOverflowError(Exception):
    """Raised when attempting to insert into a full reorder buffer."""

    pass
