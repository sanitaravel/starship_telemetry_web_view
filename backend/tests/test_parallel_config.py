"""Unit and property-based tests for ParallelPipelineConfig and FPSMeter."""

import itertools

from hypothesis import assume, given
from hypothesis import strategies as st

from src.parallel_config import FPSMeter, ParallelPipelineConfig


class TestParallelPipelineConfig:
    """Tests for ParallelPipelineConfig validation."""

    def test_default_config_is_valid(self) -> None:
        """Default configuration should pass validation with no errors."""
        config = ParallelPipelineConfig()
        assert config.validate() == []

    def test_executor_max_workers_below_range(self) -> None:
        """executor_max_workers below 1 should produce an error."""
        config = ParallelPipelineConfig(executor_max_workers=0)
        errors = config.validate()
        assert any("executor_max_workers" in e for e in errors)

    def test_executor_max_workers_above_range(self) -> None:
        """executor_max_workers above 8 should produce an error."""
        config = ParallelPipelineConfig(executor_max_workers=9)
        errors = config.validate()
        assert any("executor_max_workers" in e for e in errors)

    def test_executor_max_workers_boundary_valid(self) -> None:
        """executor_max_workers at 1 and 8 should be valid."""
        assert ParallelPipelineConfig(executor_max_workers=1).validate() == []
        assert ParallelPipelineConfig(executor_max_workers=8).validate() == []

    def test_concurrency_limit_below_range(self) -> None:
        """concurrency_limit below 1 should produce an error."""
        config = ParallelPipelineConfig(concurrency_limit=0)
        errors = config.validate()
        assert any("concurrency_limit" in e for e in errors)

    def test_concurrency_limit_above_range(self) -> None:
        """concurrency_limit above 10 should produce an error."""
        config = ParallelPipelineConfig(concurrency_limit=11)
        errors = config.validate()
        assert any("concurrency_limit" in e for e in errors)

    def test_concurrency_limit_boundary_valid(self) -> None:
        """concurrency_limit at 1 and 10 should be valid."""
        assert ParallelPipelineConfig(concurrency_limit=1).validate() == []
        assert ParallelPipelineConfig(concurrency_limit=10).validate() == []

    def test_stage_timeout_zero(self) -> None:
        """stage_timeout_seconds of 0 should produce an error."""
        config = ParallelPipelineConfig(stage_timeout_seconds=0.0)
        errors = config.validate()
        assert any("stage_timeout_seconds" in e for e in errors)

    def test_stage_timeout_negative(self) -> None:
        """stage_timeout_seconds negative should produce an error."""
        config = ParallelPipelineConfig(stage_timeout_seconds=-1.0)
        errors = config.validate()
        assert any("stage_timeout_seconds" in e for e in errors)

    def test_frame_timeout_zero(self) -> None:
        """frame_timeout_seconds of 0 should produce an error."""
        config = ParallelPipelineConfig(frame_timeout_seconds=0.0)
        errors = config.validate()
        assert any("frame_timeout_seconds" in e for e in errors)

    def test_frame_timeout_negative(self) -> None:
        """frame_timeout_seconds negative should produce an error."""
        config = ParallelPipelineConfig(frame_timeout_seconds=-1.0)
        errors = config.validate()
        assert any("frame_timeout_seconds" in e for e in errors)

    def test_multiple_errors_reported(self) -> None:
        """Multiple invalid values should produce multiple errors."""
        config = ParallelPipelineConfig(
            executor_max_workers=0,
            concurrency_limit=0,
            stage_timeout_seconds=-1.0,
            frame_timeout_seconds=-1.0,
        )
        errors = config.validate()
        assert len(errors) == 4


class TestFPSMeter:
    """Tests for FPSMeter sliding window FPS computation."""

    def test_no_broadcasts_returns_zero(self) -> None:
        """FPS is 0.0 when no broadcasts have been recorded."""
        meter = FPSMeter()
        assert meter.get_fps() == 0.0

    def test_single_broadcast_returns_zero(self) -> None:
        """FPS is 0.0 when only one broadcast has been recorded."""
        meter = FPSMeter()
        meter.record_broadcast(1.0)
        assert meter.get_fps() == 0.0

    def test_two_broadcasts_one_second_apart(self) -> None:
        """Two broadcasts 1 second apart should yield 1.0 FPS."""
        meter = FPSMeter()
        meter.record_broadcast(0.0)
        meter.record_broadcast(1.0)
        assert meter.get_fps() == 1.0

    def test_ten_broadcasts_evenly_spaced(self) -> None:
        """10 broadcasts at 0.5s intervals: (10-1)/(4.5) = 2.0 FPS."""
        meter = FPSMeter()
        for i in range(10):
            meter.record_broadcast(i * 0.5)
        assert meter.get_fps() == 2.0

    def test_sliding_window_discards_old_entries(self) -> None:
        """After more than 10 entries, only last 10 are used."""
        meter = FPSMeter()
        # Record 15 entries at 1-second intervals
        for i in range(15):
            meter.record_broadcast(float(i))
        # Window is [5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
        # elapsed = 14 - 5 = 9, count-1 = 9, fps = 9/9 = 1.0
        assert meter.get_fps() == 1.0

    def test_zero_elapsed_returns_zero(self) -> None:
        """If all timestamps are identical, FPS should be 0.0."""
        meter = FPSMeter()
        meter.record_broadcast(5.0)
        meter.record_broadcast(5.0)
        assert meter.get_fps() == 0.0

    def test_fps_rounding(self) -> None:
        """FPS should be rounded to 2 decimal places."""
        meter = FPSMeter()
        meter.record_broadcast(0.0)
        meter.record_broadcast(0.3)
        meter.record_broadcast(0.6)
        # elapsed = 0.6, count-1 = 2, fps = 2/0.6 = 3.333... → 3.33
        assert meter.get_fps() == 3.33


# Feature: pipeline-parallelization, Property 2: Configuration Parameter Validation
class TestConfigParameterValidationProperty:
    """Property-based tests for configuration parameter validation.

    **Validates: Requirements 2.3, 3.3, 3.5, 7.3**
    """

    @given(value=st.integers(min_value=-1000, max_value=1000))
    def test_executor_max_workers_accepted_iff_in_valid_range(self, value: int) -> None:
        """executor_max_workers is accepted iff value is in [1, 8]."""
        config = ParallelPipelineConfig(executor_max_workers=value)
        errors = config.validate()
        worker_errors = [e for e in errors if "executor_max_workers" in e]

        if 1 <= value <= 8:
            assert worker_errors == [], (
                f"Expected no executor_max_workers error for value {value}, got {worker_errors}"
            )
        else:
            assert len(worker_errors) > 0, (
                f"Expected executor_max_workers error for value {value}, got none"
            )

    @given(value=st.integers(min_value=-1000, max_value=1000))
    def test_concurrency_limit_accepted_iff_in_valid_range(self, value: int) -> None:
        """concurrency_limit is accepted iff value is in [1, 10]."""
        config = ParallelPipelineConfig(concurrency_limit=value)
        errors = config.validate()
        limit_errors = [e for e in errors if "concurrency_limit" in e]

        if 1 <= value <= 10:
            assert limit_errors == [], (
                f"Expected no concurrency_limit error for value {value}, got {limit_errors}"
            )
        else:
            assert len(limit_errors) > 0, (
                f"Expected concurrency_limit error for value {value}, got none"
            )


# Feature: pipeline-parallelization, Property 11: FPS Sliding Window Calculation
class TestFPSSlidingWindowCalculationProperty:
    """Property-based tests for FPS sliding window calculation.

    **Validates: Requirements 6.1, 6.4**
    """

    @given(
        increments=st.lists(
            st.floats(min_value=0.01, max_value=100.0),
            min_size=0,
            max_size=20,
        )
    )
    def test_fps_equals_formula_over_sliding_window(
        self, increments: list[float]
    ) -> None:
        """FPS equals (count-1)/(last-first) over most recent 10 entries, rounded to 2dp.

        If fewer than 2 broadcasts, FPS is 0.0.
        """
        # Build monotonically increasing timestamps from cumulative sums
        timestamps = list(itertools.accumulate(increments, initial=0.0))[1:]

        meter = FPSMeter()
        for ts in timestamps:
            meter.record_broadcast(ts)

        # Determine the effective window (last 10 entries)
        window = timestamps[-10:] if len(timestamps) > 10 else timestamps

        if len(window) < 2:
            assert meter.get_fps() == 0.0
        else:
            elapsed = window[-1] - window[0]
            if elapsed <= 0:
                assert meter.get_fps() == 0.0
            else:
                expected_fps = round((len(window) - 1) / elapsed, 2)
                assert meter.get_fps() == expected_fps

    @given(
        increments=st.lists(
            st.floats(min_value=0.01, max_value=100.0),
            min_size=11,
            max_size=30,
        )
    )
    def test_sliding_window_keeps_only_last_10(self, increments: list[float]) -> None:
        """The sliding window retains only the last 10 entries regardless of total broadcasts."""
        timestamps = list(itertools.accumulate(increments, initial=0.0))[1:]

        meter = FPSMeter()
        for ts in timestamps:
            meter.record_broadcast(ts)

        # Only the last 10 timestamps matter
        window = timestamps[-10:]
        elapsed = window[-1] - window[0]
        assume(elapsed > 0)

        expected_fps = round((len(window) - 1) / elapsed, 2)
        assert meter.get_fps() == expected_fps

    @given(
        single_ts=st.floats(min_value=0.0, max_value=1000.0),
    )
    def test_fewer_than_two_broadcasts_returns_zero(self, single_ts: float) -> None:
        """FPS is 0.0 when fewer than 2 broadcasts have been recorded."""
        # Zero broadcasts
        meter_empty = FPSMeter()
        assert meter_empty.get_fps() == 0.0

        # One broadcast
        meter_one = FPSMeter()
        meter_one.record_broadcast(single_ts)
        assert meter_one.get_fps() == 0.0
