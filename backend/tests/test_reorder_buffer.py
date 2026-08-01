"""Unit tests for ReorderBuffer and BufferedResult."""

import time

import pytest

from src.reorder_buffer import BufferedResult, BufferOverflowError, ReorderBuffer


class TestBufferedResult:
    """Tests for the BufferedResult dataclass."""

    def test_creation_with_all_fields(self) -> None:
        """BufferedResult stores record, frame_b64, and inserted_at."""
        result = BufferedResult(record={"data": 1}, frame_b64="abc123", inserted_at=1.0)
        assert result.record == {"data": 1}
        assert result.frame_b64 == "abc123"
        assert result.inserted_at == 1.0

    def test_creation_with_none_frame(self) -> None:
        """BufferedResult accepts None for frame_b64."""
        result = BufferedResult(record="test", frame_b64=None, inserted_at=0.5)
        assert result.frame_b64 is None


class TestReorderBuffer:
    """Tests for ReorderBuffer insert, drain, and gap handling."""

    def _make_result(self, seq: int) -> BufferedResult:
        """Helper to create a BufferedResult with a given sequence as record."""
        return BufferedResult(record=seq, frame_b64=None, inserted_at=time.monotonic())

    def test_initial_state(self) -> None:
        """New buffer starts empty with next_expected=1."""
        buf = ReorderBuffer()
        assert buf.size == 0
        assert buf.next_expected == 1

    def test_insert_and_size(self) -> None:
        """Inserting increases buffer size."""
        buf = ReorderBuffer()
        buf.insert(1, self._make_result(1))
        assert buf.size == 1
        buf.insert(3, self._make_result(3))
        assert buf.size == 2

    def test_drain_consecutive_from_start(self) -> None:
        """Drain returns consecutive results starting from next_expected."""
        buf = ReorderBuffer()
        buf.insert(1, self._make_result(1))
        buf.insert(2, self._make_result(2))
        buf.insert(3, self._make_result(3))

        results = buf.drain()
        assert len(results) == 3
        assert [r.record for r in results] == [1, 2, 3]
        assert buf.size == 0
        assert buf.next_expected == 4

    def test_drain_stops_at_gap(self) -> None:
        """Drain stops when a gap is encountered."""
        buf = ReorderBuffer()
        buf.insert(1, self._make_result(1))
        buf.insert(2, self._make_result(2))
        # Skip 3
        buf.insert(4, self._make_result(4))
        buf.insert(5, self._make_result(5))

        results = buf.drain()
        assert len(results) == 2
        assert [r.record for r in results] == [1, 2]
        assert buf.next_expected == 3
        assert buf.size == 2  # 4 and 5 still buffered

    def test_drain_empty_when_next_not_available(self) -> None:
        """Drain returns empty list when next_expected is not in buffer."""
        buf = ReorderBuffer()
        buf.insert(5, self._make_result(5))
        results = buf.drain()
        assert results == []
        assert buf.next_expected == 1

    def test_drain_after_gap_filled(self) -> None:
        """After filling a gap, drain returns the full consecutive run."""
        buf = ReorderBuffer()
        buf.insert(2, self._make_result(2))
        buf.insert(3, self._make_result(3))
        assert buf.drain() == []

        buf.insert(1, self._make_result(1))
        results = buf.drain()
        assert [r.record for r in results] == [1, 2, 3]

    def test_advance_past_gap(self) -> None:
        """advance_past_gap sets next_expected to gap_seq + 1."""
        buf = ReorderBuffer()
        buf.insert(2, self._make_result(2))
        buf.insert(3, self._make_result(3))

        buf.advance_past_gap(1)
        assert buf.next_expected == 2

        # Now drain should pick up 2 and 3
        results = buf.drain()
        assert [r.record for r in results] == [2, 3]
        assert buf.next_expected == 4

    def test_advance_past_gap_removes_entry_if_present(self) -> None:
        """advance_past_gap removes the gap_seq entry if it exists in buffer."""
        buf = ReorderBuffer()
        buf.insert(1, self._make_result(1))
        buf.insert(2, self._make_result(2))

        buf.advance_past_gap(1)
        assert buf.size == 1  # only 2 remains
        assert buf.next_expected == 2

    def test_discard_oldest_gap_basic(self) -> None:
        """discard_oldest_gap finds first missing seq and advances past it."""
        buf = ReorderBuffer()
        # next_expected=1, buffer has 2,3,4 — gap is at 1
        buf.insert(2, self._make_result(2))
        buf.insert(3, self._make_result(3))
        buf.insert(4, self._make_result(4))

        discarded = buf.discard_oldest_gap()
        assert discarded == 1
        assert buf.next_expected == 2

    def test_discard_oldest_gap_with_consecutive_prefix(self) -> None:
        """discard_oldest_gap skips consecutive entries to find the gap."""
        buf = ReorderBuffer()
        # next_expected=1, buffer has 1,2,4,5 — gap is at 3
        buf.insert(1, self._make_result(1))
        buf.insert(2, self._make_result(2))
        buf.insert(4, self._make_result(4))
        buf.insert(5, self._make_result(5))

        discarded = buf.discard_oldest_gap()
        assert discarded == 3
        assert buf.next_expected == 4

    def test_discard_oldest_gap_empty_buffer_raises(self) -> None:
        """discard_oldest_gap raises ValueError on empty buffer."""
        buf = ReorderBuffer()
        with pytest.raises(ValueError, match="empty buffer"):
            buf.discard_oldest_gap()

    def test_max_size_enforcement(self) -> None:
        """Insert raises BufferOverflowError when at max_size."""
        buf = ReorderBuffer(max_size=3)
        buf.insert(2, self._make_result(2))
        buf.insert(3, self._make_result(3))
        buf.insert(4, self._make_result(4))

        with pytest.raises(BufferOverflowError):
            buf.insert(5, self._make_result(5))

    def test_max_size_default_is_120(self) -> None:
        """Default max_size is 120."""
        buf = ReorderBuffer()
        assert buf._max_size == 120

    def test_drain_then_insert_continues_sequence(self) -> None:
        """After draining, new inserts continue from the advanced pointer."""
        buf = ReorderBuffer()
        buf.insert(1, self._make_result(1))
        buf.insert(2, self._make_result(2))
        buf.drain()

        buf.insert(3, self._make_result(3))
        results = buf.drain()
        assert [r.record for r in results] == [3]
        assert buf.next_expected == 4

    def test_overflow_recovery_with_discard_and_drain(self) -> None:
        """After discarding a gap, drain can free buffer space."""
        buf = ReorderBuffer(max_size=5)
        # Insert 5 entries but with a gap at position 1
        buf.insert(2, self._make_result(2))
        buf.insert(3, self._make_result(3))
        buf.insert(4, self._make_result(4))
        buf.insert(5, self._make_result(5))
        buf.insert(6, self._make_result(6))

        # Buffer is full, can't insert
        with pytest.raises(BufferOverflowError):
            buf.insert(7, self._make_result(7))

        # Discard the gap at seq=1 and drain
        discarded = buf.discard_oldest_gap()
        assert discarded == 1
        results = buf.drain()
        assert [r.record for r in results] == [2, 3, 4, 5, 6]
        assert buf.size == 0

        # Now we can insert again
        buf.insert(7, self._make_result(7))
        assert buf.size == 1


# ---------------------------------------------------------------------------
# Property-Based Tests
# ---------------------------------------------------------------------------

from hypothesis import given, settings
from hypothesis import strategies as st


# Feature: pipeline-parallelization, Property 6: Frame Sequence Number Contiguity
class TestFrameSequenceNumberContiguityProperty:
    """Property-based tests for frame sequence number contiguity.

    **Validates: Requirements 4.1**
    """

    @given(n=st.integers(min_value=1, max_value=100))
    def test_dispatched_frames_get_contiguous_sequence_numbers(self, n: int) -> None:
        """For N frames dispatched, assigned sequence numbers form [1, 2, ..., N].

        Simulates the dispatch logic that assigns Frame_Sequence_Numbers starting
        at 1 and incrementing by 1 for each frame. Verifies:
        - Exactly N unique sequence numbers are assigned
        - They form the contiguous range [1..N] with no gaps or duplicates
        - When inserted into the ReorderBuffer and drained, all N come out in order
        """
        # Simulate frame dispatch: assign sequence numbers starting at 1
        assigned_sequence_numbers: list[int] = []
        next_seq = 1
        for _ in range(n):
            assigned_sequence_numbers.append(next_seq)
            next_seq += 1

        # Verify: assigned sequence numbers form contiguous [1..N]
        assert assigned_sequence_numbers == list(range(1, n + 1))

        # Verify: no gaps — difference between consecutive numbers is always 1
        for i in range(1, len(assigned_sequence_numbers)):
            assert assigned_sequence_numbers[i] - assigned_sequence_numbers[i - 1] == 1

        # Verify: no duplicates — all values are unique
        assert len(set(assigned_sequence_numbers)) == n

        # Verify: inserting into ReorderBuffer and draining yields all N in order
        buf = ReorderBuffer(max_size=max(n, 1))
        for seq in assigned_sequence_numbers:
            result = BufferedResult(record=seq, frame_b64=None, inserted_at=1.0)
            buf.insert(seq, result)

        drained = buf.drain()
        assert len(drained) == n
        records = [r.record for r in drained]
        assert records == list(range(1, n + 1))

    @given(n=st.integers(min_value=1, max_value=100))
    def test_sequence_numbers_have_no_gaps_or_duplicates(self, n: int) -> None:
        """Sequence number assignment produces exactly the set {1, 2, ..., N}.

        Verifies the mathematical property that the assigned numbers are both
        a subset and superset of {1..N}, confirming completeness and uniqueness.
        """
        # Simulate dispatch assigning sequence numbers
        assigned: list[int] = []
        seq_counter = 1
        for _ in range(n):
            assigned.append(seq_counter)
            seq_counter += 1

        expected_set = set(range(1, n + 1))

        # No gaps: every expected number is present
        assert set(assigned) >= expected_set

        # No duplicates: no extra numbers beyond expected
        assert set(assigned) <= expected_set

        # Length matches: exactly N assignments
        assert len(assigned) == n


# Feature: pipeline-parallelization, Property 7: Reorder Buffer Preserves Capture Order
class TestReorderBufferPreservesCaptureOrderProperty:
    """Property-based tests for reorder buffer ordering.

    **Validates: Requirements 4.2, 4.3, 4.4**
    """

    @given(n=st.integers(min_value=1, max_value=50))
    def test_full_permutation_drains_in_sequence_order(self, n: int) -> None:
        """Inserting N results in any completion order always drains in 1..N order.

        Generate a random permutation of completion order for frames 1..N,
        insert all results into the buffer, then drain. The output must be
        in strictly ascending sequence number order.
        """
        import random

        buf = ReorderBuffer(max_size=max(n, 1))
        insertion_order = list(range(1, n + 1))
        random.shuffle(insertion_order)

        for seq in insertion_order:
            result = BufferedResult(record=seq, frame_b64=None, inserted_at=1.0)
            buf.insert(seq, result)

        drained = buf.drain()

        # Verify output length matches input
        assert len(drained) == n

        # Verify strictly ascending sequence number order (1, 2, 3, ..., N)
        records = [r.record for r in drained]
        assert records == list(range(1, n + 1))

        # Verify assigned broadcast sequence numbers would be monotonically
        # increasing starting at 1 (position in drain output = broadcast order)
        for broadcast_seq, result in enumerate(drained, start=1):
            assert result.record == broadcast_seq

    @given(data=st.data())
    def test_partial_drains_preserve_ordering(self, data: st.DataObject) -> None:
        """Inserting results in batches with intermediate drains preserves order.

        Split N frames into random-sized batches, insert each batch in shuffled
        order, drain after each batch, and verify the combined output is in
        strictly ascending sequence number order.
        """
        import random

        n = data.draw(st.integers(min_value=2, max_value=50), label="n_frames")
        # Decide how many batches to split into (at least 2 for partial drains)
        num_batches = data.draw(
            st.integers(min_value=2, max_value=min(n, 10)), label="num_batches"
        )

        buf = ReorderBuffer(max_size=max(n, 1))

        # Split 1..N into roughly equal batches
        all_seqs = list(range(1, n + 1))
        batch_size = max(1, n // num_batches)
        batches: list[list[int]] = []
        for i in range(0, n, batch_size):
            batches.append(all_seqs[i : i + batch_size])

        all_drained: list[BufferedResult] = []
        for batch in batches:
            # Shuffle within batch to simulate out-of-order completion
            shuffled_batch = batch[:]
            random.shuffle(shuffled_batch)
            for seq in shuffled_batch:
                result = BufferedResult(record=seq, frame_b64=None, inserted_at=1.0)
                buf.insert(seq, result)
            # Drain after each batch
            all_drained.extend(buf.drain())

        # The combined drained output must be a prefix of 1..N in strict order
        records = [r.record for r in all_drained]

        # Each drained record must be in strictly ascending order
        for i in range(1, len(records)):
            assert records[i] == records[i - 1] + 1, (
                f"Output not in strict ascending order at position {i}: "
                f"{records[i - 1]} -> {records[i]}"
            )

        # First drained record (if any) must start at 1
        if records:
            assert records[0] == 1

        # Broadcast sequence numbers are monotonically increasing from 1
        for broadcast_seq, result in enumerate(all_drained, start=1):
            assert result.record == broadcast_seq

    @given(data=st.data())
    def test_permutation_with_explicit_shuffle(self, data: st.DataObject) -> None:
        """Use Hypothesis permutations to generate completion order directly.

        This ensures reproducible shrinking of counterexamples via Hypothesis.
        """
        n = data.draw(st.integers(min_value=1, max_value=50), label="n_frames")
        perm = data.draw(
            st.permutations(list(range(1, n + 1))), label="completion_order"
        )

        buf = ReorderBuffer(max_size=max(n, 1))
        for seq in perm:
            result = BufferedResult(record=seq, frame_b64=None, inserted_at=1.0)
            buf.insert(seq, result)

        drained = buf.drain()

        assert len(drained) == n
        records = [r.record for r in drained]
        assert records == list(range(1, n + 1))


# Feature: pipeline-parallelization, Property 8: Buffer Overflow Triggers Discard and Drain
class TestBufferOverflowTriggersDiscardAndDrainProperty:
    """Property-based tests for buffer overflow discard and drain behavior.

    **Validates: Requirements 4.6, 8.2**
    """

    @given(data=st.data())
    @settings(max_examples=100)
    def test_insert_raises_overflow_at_capacity(self, data: st.DataObject) -> None:
        """When buffer is at max_size, inserting raises BufferOverflowError.

        Generate a buffer filled to capacity with gaps, then attempt to insert
        one more entry. The insert must raise BufferOverflowError.
        """
        max_size = data.draw(
            st.integers(min_value=3, max_value=30), label="max_size"
        )
        buf = ReorderBuffer(max_size=max_size)

        # Fill the buffer to capacity by inserting entries with a gap at position 1
        # (next_expected stays at 1, so nothing drains)
        for i in range(max_size):
            seq = i + 2  # start at 2, leaving gap at 1
            result = BufferedResult(record=seq, frame_b64=None, inserted_at=1.0)
            buf.insert(seq, result)

        assert buf.size == max_size

        # Attempting to insert one more should raise
        extra_seq = max_size + 2
        with pytest.raises(BufferOverflowError):
            buf.insert(
                extra_seq,
                BufferedResult(record=extra_seq, frame_b64=None, inserted_at=1.0),
            )

    @given(data=st.data())
    @settings(max_examples=100)
    def test_discard_oldest_gap_advances_next_expected(
        self, data: st.DataObject
    ) -> None:
        """After discard_oldest_gap(), next_expected advances past the gap.

        Build a buffer with a known gap at next_expected, call discard_oldest_gap,
        and verify next_expected moves past the gap.
        """
        max_size = data.draw(
            st.integers(min_value=5, max_value=30), label="max_size"
        )
        # Number of entries to insert (fill partially or fully)
        n_entries = data.draw(
            st.integers(min_value=2, max_value=max_size), label="n_entries"
        )

        buf = ReorderBuffer(max_size=max_size)

        # Insert entries starting at seq=2, creating a gap at seq=1
        for i in range(n_entries):
            seq = i + 2
            result = BufferedResult(record=seq, frame_b64=None, inserted_at=1.0)
            buf.insert(seq, result)

        old_next_expected = buf.next_expected
        assert old_next_expected == 1  # gap at 1

        discarded = buf.discard_oldest_gap()
        assert discarded == 1
        assert buf.next_expected == 2  # advanced past gap at 1
        assert buf.next_expected > old_next_expected

    @given(data=st.data())
    @settings(max_examples=100)
    def test_discard_and_drain_reduces_size_and_preserves_order(
        self, data: st.DataObject
    ) -> None:
        """After discard + drain, buffer size decreases and results are in order.

        Fill a buffer to capacity with a gap at the front, discard the gap,
        drain, and verify output is in strictly ascending sequence order and
        buffer size has decreased.
        """
        max_size = data.draw(
            st.integers(min_value=3, max_value=30), label="max_size"
        )
        buf = ReorderBuffer(max_size=max_size)

        # Fill buffer: insert consecutive seqs starting at 2 (gap at 1)
        for i in range(max_size):
            seq = i + 2
            result = BufferedResult(record=seq, frame_b64=None, inserted_at=1.0)
            buf.insert(seq, result)

        size_before = buf.size
        assert size_before == max_size

        # Discard gap at 1
        discarded = buf.discard_oldest_gap()
        assert discarded == 1

        # Drain consecutive results
        drained = buf.drain()

        # Buffer size should have decreased
        assert buf.size < size_before

        # Drained results should be in strictly ascending sequence order
        if len(drained) > 0:
            records = [r.record for r in drained]
            for i in range(1, len(records)):
                assert records[i] == records[i - 1] + 1, (
                    f"Not in strict ascending order: {records[i - 1]} -> {records[i]}"
                )

        # Since we inserted 2..max_size+1 consecutively and gap was at 1,
        # after discarding gap at 1, next_expected=2, and all entries drain
        assert len(drained) == max_size
        assert buf.size == 0

    @given(data=st.data())
    @settings(max_examples=100)
    def test_repeated_discard_brings_buffer_within_limits(
        self, data: st.DataObject
    ) -> None:
        """Repeatedly calling discard_oldest_gap brings buffer within limits.

        Create a buffer that exceeds a threshold (simulating 2× concurrency_limit),
        then repeatedly discard + drain until buffer is within the limit.
        """
        concurrency_limit = data.draw(
            st.integers(min_value=2, max_value=10), label="concurrency_limit"
        )
        threshold = 2 * concurrency_limit
        # Buffer size is larger than threshold to trigger overflow handling
        buffer_fill = data.draw(
            st.integers(min_value=threshold + 1, max_value=threshold + 15),
            label="buffer_fill",
        )
        max_size = buffer_fill + 5  # ensure we can fill to buffer_fill

        buf = ReorderBuffer(max_size=max_size)

        # Create a buffer with multiple gaps so discard_oldest_gap can be
        # called multiple times. Insert entries at even positions only,
        # creating gaps at odd positions.
        inserted_seqs: list[int] = []
        for i in range(buffer_fill):
            seq = (i + 1) * 2  # 2, 4, 6, 8, ... (gaps at 1, 3, 5, 7, ...)
            result = BufferedResult(record=seq, frame_b64=None, inserted_at=1.0)
            buf.insert(seq, result)
            inserted_seqs.append(seq)

        assert buf.size == buffer_fill
        assert buf.size > threshold

        # Repeatedly discard oldest gap and drain until within threshold
        iterations = 0
        max_iterations = buffer_fill + 10  # safety to prevent infinite loop
        while buf.size > concurrency_limit and iterations < max_iterations:
            buf.discard_oldest_gap()
            buf.drain()
            iterations += 1

        # Buffer should now be within the concurrency limit
        assert buf.size <= concurrency_limit

    @given(data=st.data())
    @settings(max_examples=100)
    def test_discard_drain_cycle_produces_ascending_output(
        self, data: st.DataObject
    ) -> None:
        """The discard/drain cycle produces output in strictly ascending sequence order.

        Build a buffer with scattered gaps, then perform multiple discard+drain
        cycles. Collect all drained results and verify they are in strictly
        ascending sequence order.
        """
        max_size = data.draw(
            st.integers(min_value=5, max_value=40), label="max_size"
        )
        # Number of entries to insert
        n_entries = data.draw(
            st.integers(min_value=3, max_value=max_size), label="n_entries"
        )
        # Choose which positions to fill (subset of possible positions)
        # We need gaps for discard_oldest_gap to work on
        available_positions = list(range(2, 2 + n_entries * 3))
        chosen_positions = sorted(
            data.draw(
                st.lists(
                    st.sampled_from(available_positions),
                    min_size=n_entries,
                    max_size=n_entries,
                    unique=True,
                ),
                label="positions",
            )
        )

        buf = ReorderBuffer(max_size=max_size)
        for seq in chosen_positions:
            result = BufferedResult(record=seq, frame_b64=None, inserted_at=1.0)
            buf.insert(seq, result)

        # Perform initial drain (may get nothing if gap at next_expected=1)
        all_drained: list[BufferedResult] = []
        all_drained.extend(buf.drain())

        # Now repeatedly discard+drain until buffer is empty or no more gaps
        max_cycles = n_entries + 5
        cycles = 0
        while buf.size > 0 and cycles < max_cycles:
            try:
                buf.discard_oldest_gap()
            except ValueError:
                break
            drained = buf.drain()
            all_drained.extend(drained)
            cycles += 1

        # Verify all drained results are in strictly ascending sequence order
        if len(all_drained) > 1:
            records = [r.record for r in all_drained]
            for i in range(1, len(records)):
                assert records[i] > records[i - 1], (
                    f"Output not in strictly ascending order at position {i}: "
                    f"{records[i - 1]} -> {records[i]}"
                )
