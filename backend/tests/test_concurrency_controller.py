"""Unit tests and property-based tests for ConcurrencyController."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.concurrency_controller import ConcurrencyController


class TestConcurrencyController:
    """Tests for ConcurrencyController slot acquisition and release."""

    def test_initial_state(self) -> None:
        """New controller starts with zero in-flight and default limit of 3."""
        ctrl = ConcurrencyController()
        assert ctrl.in_flight == 0
        assert ctrl.limit == 3

    def test_custom_limit(self) -> None:
        """Controller accepts a custom initial limit."""
        ctrl = ConcurrencyController(limit=5)
        assert ctrl.limit == 5
        assert ctrl.in_flight == 0

    def test_try_acquire_succeeds_below_limit(self) -> None:
        """try_acquire returns True when in-flight < limit."""
        ctrl = ConcurrencyController(limit=2)
        assert ctrl.try_acquire() is True
        assert ctrl.in_flight == 1
        assert ctrl.try_acquire() is True
        assert ctrl.in_flight == 2

    def test_try_acquire_fails_at_limit(self) -> None:
        """try_acquire returns False when in-flight == limit."""
        ctrl = ConcurrencyController(limit=2)
        ctrl.try_acquire()
        ctrl.try_acquire()
        assert ctrl.try_acquire() is False
        assert ctrl.in_flight == 2  # unchanged

    def test_release_decrements_in_flight(self) -> None:
        """release decrements the in-flight count."""
        ctrl = ConcurrencyController(limit=3)
        ctrl.try_acquire()
        ctrl.try_acquire()
        assert ctrl.in_flight == 2

        ctrl.release()
        assert ctrl.in_flight == 1

        ctrl.release()
        assert ctrl.in_flight == 0

    def test_release_does_not_go_below_zero(self) -> None:
        """release does not decrement below zero."""
        ctrl = ConcurrencyController(limit=3)
        ctrl.release()
        assert ctrl.in_flight == 0

    def test_acquire_after_release(self) -> None:
        """After releasing, a new slot can be acquired."""
        ctrl = ConcurrencyController(limit=1)
        assert ctrl.try_acquire() is True
        assert ctrl.try_acquire() is False

        ctrl.release()
        assert ctrl.try_acquire() is True

    def test_set_limit_valid_values(self) -> None:
        """set_limit accepts values in [1, 10] and updates the limit."""
        ctrl = ConcurrencyController(limit=3)

        assert ctrl.set_limit(1) is True
        assert ctrl.limit == 1

        assert ctrl.set_limit(10) is True
        assert ctrl.limit == 10

        assert ctrl.set_limit(5) is True
        assert ctrl.limit == 5

    def test_set_limit_rejects_zero(self) -> None:
        """set_limit rejects 0 and retains previous value."""
        ctrl = ConcurrencyController(limit=3)
        assert ctrl.set_limit(0) is False
        assert ctrl.limit == 3

    def test_set_limit_rejects_negative(self) -> None:
        """set_limit rejects negative values and retains previous value."""
        ctrl = ConcurrencyController(limit=3)
        assert ctrl.set_limit(-1) is False
        assert ctrl.limit == 3

    def test_set_limit_rejects_above_ten(self) -> None:
        """set_limit rejects values > 10 and retains previous value."""
        ctrl = ConcurrencyController(limit=3)
        assert ctrl.set_limit(11) is False
        assert ctrl.limit == 3

    def test_set_limit_rejects_non_integer(self) -> None:
        """set_limit rejects non-integer types and retains previous value."""
        ctrl = ConcurrencyController(limit=3)
        assert ctrl.set_limit(3.5) is False  # type: ignore[arg-type]
        assert ctrl.limit == 3

    def test_set_limit_affects_future_acquisitions(self) -> None:
        """Changing the limit affects subsequent try_acquire calls."""
        ctrl = ConcurrencyController(limit=2)
        ctrl.try_acquire()
        ctrl.try_acquire()
        assert ctrl.try_acquire() is False

        # Increase limit to allow more
        ctrl.set_limit(4)
        assert ctrl.try_acquire() is True
        assert ctrl.in_flight == 3

    def test_set_limit_below_current_in_flight(self) -> None:
        """Setting limit below current in-flight doesn't release slots.

        The existing in-flight frames continue, but no new frames are admitted
        until in-flight drops below the new limit.
        """
        ctrl = ConcurrencyController(limit=5)
        for _ in range(4):
            ctrl.try_acquire()
        assert ctrl.in_flight == 4

        # Lower the limit to 2 — existing in-flight is preserved
        assert ctrl.set_limit(2) is True
        assert ctrl.in_flight == 4
        assert ctrl.limit == 2

        # Cannot acquire new slots since in_flight (4) >= new limit (2)
        assert ctrl.try_acquire() is False

        # Release slots until below new limit
        ctrl.release()
        ctrl.release()
        ctrl.release()
        assert ctrl.in_flight == 1
        assert ctrl.try_acquire() is True
        assert ctrl.in_flight == 2
        assert ctrl.try_acquire() is False

    def test_full_cycle_acquire_release(self) -> None:
        """Full cycle: fill to capacity, release all, fill again."""
        ctrl = ConcurrencyController(limit=3)

        # Fill to capacity
        for _ in range(3):
            assert ctrl.try_acquire() is True
        assert ctrl.try_acquire() is False

        # Release all
        for _ in range(3):
            ctrl.release()
        assert ctrl.in_flight == 0

        # Fill again
        for _ in range(3):
            assert ctrl.try_acquire() is True
        assert ctrl.in_flight == 3


# Feature: pipeline-parallelization, Property 4: Admission Control Invariant
class TestAdmissionControlInvariantProperty:
    """Property-based tests for admission control invariant.

    For any pipeline state, when a new frame arrives: if the number of in-flight
    frames is strictly less than the concurrency limit, the frame SHALL be dispatched
    for processing; if the number of in-flight frames equals the concurrency limit,
    the frame SHALL be discarded.

    **Validates: Requirements 3.1, 3.2**
    """

    @given(
        limit=st.integers(min_value=1, max_value=10),
        in_flight=st.integers(min_value=0, max_value=15),
    )
    def test_frame_dispatched_when_in_flight_below_limit(
        self, limit: int, in_flight: int
    ) -> None:
        """try_acquire returns True iff in_flight < limit, False when in_flight >= limit."""
        ctrl = ConcurrencyController(limit=limit)

        # Simulate the controller already having `in_flight` slots acquired
        # We can only acquire up to `limit` slots via try_acquire, so clamp
        slots_to_fill = min(in_flight, limit)
        for _ in range(slots_to_fill):
            ctrl.try_acquire()

        # If we couldn't fill to the desired in_flight (because it exceeds limit),
        # the controller is at capacity already
        actual_in_flight = ctrl.in_flight
        assert actual_in_flight == slots_to_fill

        # Now attempt to acquire one more slot (simulating a new frame arrival)
        result = ctrl.try_acquire()

        if actual_in_flight < limit:
            # Frame SHALL be dispatched for processing
            assert result is True, (
                f"Expected frame dispatched (True) when in_flight={actual_in_flight} < limit={limit}"
            )
            assert ctrl.in_flight == actual_in_flight + 1
        else:
            # Frame SHALL be discarded (at capacity)
            assert result is False, (
                f"Expected frame discarded (False) when in_flight={actual_in_flight} >= limit={limit}"
            )
            assert ctrl.in_flight == actual_in_flight  # unchanged

    @given(
        limit=st.integers(min_value=1, max_value=10),
        n_acquires=st.integers(min_value=0, max_value=15),
    )
    def test_exactly_limit_frames_admitted_then_all_discarded(
        self, limit: int, n_acquires: int
    ) -> None:
        """After exactly `limit` successful acquires, all subsequent attempts are discarded."""
        ctrl = ConcurrencyController(limit=limit)

        admitted = 0
        discarded = 0

        for _ in range(n_acquires):
            if ctrl.try_acquire():
                admitted += 1
            else:
                discarded += 1

        # Admitted count should be min(n_acquires, limit)
        expected_admitted = min(n_acquires, limit)
        expected_discarded = max(0, n_acquires - limit)

        assert admitted == expected_admitted, (
            f"Expected {expected_admitted} admitted, got {admitted} "
            f"(limit={limit}, n_acquires={n_acquires})"
        )
        assert discarded == expected_discarded, (
            f"Expected {expected_discarded} discarded, got {discarded} "
            f"(limit={limit}, n_acquires={n_acquires})"
        )
        assert ctrl.in_flight == expected_admitted


# Feature: pipeline-parallelization, Property 5: In-Flight Count Conservation
class TestInFlightCountConservationProperty:
    """Property-based tests for in-flight count conservation.

    For any sequence of frame dispatch and completion events, the in-flight
    frame count SHALL equal the number of dispatched frames minus the number
    of completed (or cancelled) frames, and SHALL never be negative or exceed
    the concurrency limit.

    **Validates: Requirements 3.4**
    """

    @given(
        limit=st.integers(min_value=1, max_value=10),
        events=st.lists(
            st.sampled_from(["dispatch", "complete", "cancel"]),
            min_size=0,
            max_size=50,
        ),
    )
    @settings(max_examples=100)
    def test_in_flight_equals_dispatched_minus_completed(
        self, limit: int, events: list[str]
    ) -> None:
        """in_flight == dispatched - completed at every point, never negative or > limit."""
        ctrl = ConcurrencyController(limit=limit)

        dispatched = 0
        completed = 0

        for event in events:
            if event == "dispatch":
                if ctrl.try_acquire():
                    dispatched += 1
            elif event in ("complete", "cancel"):
                if ctrl.in_flight > 0:
                    ctrl.release()
                    completed += 1

            # Invariant: in_flight == dispatched - completed
            assert ctrl.in_flight == dispatched - completed, (
                f"in_flight={ctrl.in_flight} != dispatched({dispatched}) - completed({completed})"
            )
            # Invariant: in_flight is never negative
            assert ctrl.in_flight >= 0, (
                f"in_flight={ctrl.in_flight} went negative"
            )
            # Invariant: in_flight never exceeds limit
            assert ctrl.in_flight <= ctrl.limit, (
                f"in_flight={ctrl.in_flight} exceeded limit={ctrl.limit}"
            )
