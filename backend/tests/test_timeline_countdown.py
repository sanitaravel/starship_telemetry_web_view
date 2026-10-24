"""Tests for the countdown clock before T-0 (src.timeline.countdown) and its place in the snapshot."""

from hypothesis import given
from hypothesis import strategies as st

from src.timeline.countdown import (
    EXTRAPOLATED,
    HUD_CLOCK,
    TOP_CLOCK,
    CountdownTracker,
    clock_reading,
)
from src.timeline.service import TimelineService


def feed(tracker, readings, start=0.0, step=1.0, roi=TOP_CLOCK):
    """Push readings one video second apart; returns the ticks."""
    return [tracker.push(start + i * step, r, roi) for i, r in enumerate(readings)]


# --- clock_reading ---


def test_hud_clock_counts_only_with_a_minus_sign():
    assert clock_reading("00:00:27", "-", None) == (-27, HUD_CLOCK)
    assert clock_reading("00:00:27", "+", None) is None  # after liftoff
    assert clock_reading("00:00:27", None, None) is None  # sign unreadable


def test_top_clock_is_signed_and_the_hud_clock_wins():
    assert clock_reading(None, None, "-00:12:31") == (-751, TOP_CLOCK)
    assert clock_reading(None, None, "+00:00:05") is None
    assert clock_reading("00:00:27", "-", "-00:00:30") == (-27, HUD_CLOCK)


def test_unreadable_clocks_give_nothing():
    assert clock_reading("garbage", "-", "12:31") is None
    assert clock_reading(None, None, None) is None


# --- CountdownTracker ---


def test_first_reading_needs_a_second_one_agreeing():
    tracker = CountdownTracker()
    first, second = feed(tracker, [-751, -750])
    assert first is None
    assert second is not None and second.met == -750 and not second.hold


def test_a_lone_misread_is_not_believed():
    tracker = CountdownTracker()
    ticks = feed(tracker, [-751, -750, -749, -9, -747])
    assert ticks[3].met > -749 and ticks[3].source_roi == EXTRAPOLATED  # runs on, not -9
    assert ticks[4].met == -747


def test_handoff_gap_keeps_running_without_hold():
    # Top-left clock down to T-29, no clock for ~2 s, then the HUD clock at T-27
    tracker = CountdownTracker()
    top = feed(tracker, [-32, -31, -30, -29])
    gap = [tracker.push(4.0, None), tracker.push(5.0, None)]
    hud = tracker.push(6.0, -26, HUD_CLOCK)
    assert top[-1].met == -29
    assert [t.met for t in gap] == [-28, -27]
    assert all(not t.hold and t.source_roi == EXTRAPOLATED for t in gap)
    assert hud.met == -26 and hud.source_roi == HUD_CLOCK


def test_long_gap_stops_the_clock():
    tracker = CountdownTracker()
    feed(tracker, [-600, -599])
    assert tracker.push(1.0 + 61.0, None) is None


def test_a_frozen_clock_is_a_hold_until_it_moves():
    tracker = CountdownTracker()
    ticks = feed(tracker, [-42, -41, -40, -40, -40, -40, -40, -40, -39, -38])
    held = [t.hold for t in ticks[1:]]
    assert held == [False, False, False, False, False, True, True, False, False]
    assert ticks[7].met == -40  # no extrapolation while holding
    assert ticks[8].met == -39 and ticks[9].met == -38


def test_held_clock_leaving_the_screen_runs_on_from_where_it_was_seen():
    tracker = CountdownTracker()
    feed(tracker, [-40, -40, -40, -40, -40, -40])  # holding by the end (t = 5)
    gap = tracker.push(7.0, None)
    assert gap.met == -38 and not gap.hold


def test_recycle_back_up_is_accepted_once_confirmed():
    tracker = CountdownTracker()
    feed(tracker, [-45, -44, -43])
    first = tracker.push(3.0, -600)
    second = tracker.push(4.0, -599)
    assert first.source_roi == EXTRAPOLATED and first.met > -43
    assert second.met == -599
    assert tracker.push(5.0, -598).met == -598


def test_countdown_never_passes_zero():
    tracker = CountdownTracker()
    feed(tracker, [-2, -1])
    assert tracker.push(5.0, None).met == 0.0


@given(
    start=st.integers(min_value=-6 * 3600, max_value=-100),
    step=st.floats(min_value=0.5, max_value=1.5),
    count=st.integers(min_value=2, max_value=40),
)
def test_a_running_clock_is_followed_within_a_second(start, step, count):
    tracker = CountdownTracker()
    tick = None
    for i in range(count):
        video_s = i * step
        shown = min(0, start + int(video_s))
        tick = tracker.push(video_s, shown) or tick
        if tick is not None and i >= 1:
            assert not tick.hold
            assert abs(tick.met - (start + video_s)) <= 1.0
    assert tick is not None


@given(st.lists(st.one_of(st.none(), st.integers(min_value=-7200, max_value=0)), max_size=60))
def test_ticks_are_never_positive_and_hold_only_on_a_visible_clock(readings):
    tracker = CountdownTracker()
    for i, reading in enumerate(readings):
        tick = tracker.push(float(i), reading)
        if tick is not None:
            assert tick.met <= 0
            if tick.hold:
                assert reading is not None


# --- TimelineService.on_countdown ---


def _service(events=()):
    service = TimelineService(first_revision=0)
    service.set_mission("ift-12", events)
    return service


def test_countdown_ticks_change_the_snapshot_met_and_hold():
    service = _service()
    assert service.on_countdown(-751.0)
    snap = service.snapshot()
    assert (snap["met"], snap["clock_source"], snap["hold"]) == (-751.0, "countdown", False)
    assert not service.on_countdown(-751.0)  # nothing new
    assert service.on_countdown(-751.0, hold=True)
    assert service.snapshot()["hold"] is True


def test_recycle_shows_the_clock_but_statuses_keep_the_furthest_met():
    service = _service([{"id": "go", "t": -100, "event": "Poll", "vehicle": "both", "auto": {"kind": "manual"}}])
    service.on_countdown(-50.0)
    assert service.machine.status("go").status == "pending"
    assert service.on_countdown(-600.0)
    assert service.snapshot()["met"] == -600.0
    assert service.machine.status("go").status == "pending"


def test_countdown_is_ignored_once_the_telemetry_clock_runs():
    service = _service()
    service.on_countdown(-3.0)
    service.on_record({"mission_elapsed_time": "T+00:00:01", "timestamp": 31000})
    assert not service.on_countdown(-1.0)
    snap = service.snapshot()
    assert (snap["met"], snap["clock_source"], snap["hold"]) == (1.0, "telemetry", False)


def test_new_run_forgets_the_countdown():
    service = _service()
    service.on_countdown(-30.0, hold=True)
    service.reset_run("run-2")
    snap = service.snapshot()
    assert (snap["met"], snap["clock_source"], snap["hold"]) == (None, None, False)
