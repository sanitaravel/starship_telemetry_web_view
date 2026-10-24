"""The timeline's telemetry rules on synthetic flights (src/timeline/rules.py, samples.py)."""

from hypothesis import given, settings
from hypothesis import strategies as st

from src.storage.missions import TIMELINE_RULES
from src.timeline.config import TIMELINE_CONFIG, TimelineConfig
from src.timeline.rules import RuleEngine, StableCount
from src.timeline.samples import MetFilter, RecordSample, format_met, parse_met, read_sample
from tests.timeline_records import PLANNED, Segment, flight, nominal_flight, record


def run(records, planned=PLANNED, config: TimelineConfig = TIMELINE_CONFIG) -> RuleEngine:
    engine = RuleEngine(config)
    engine.set_planned(planned)
    for payload in records:
        met = parse_met(payload["mission_elapsed_time"])
        engine.push(read_sample(payload, float(met)))
    return engine


def statuses(engine: RuleEngine) -> dict[str, str]:
    return {rule: verdict.status for rule, verdict in engine.verdicts.items()}


# --- Samples ---


def test_parse_and_format_met():
    assert parse_met("00:01:23") == 83
    assert parse_met("T+01:00:00") == 3600
    assert parse_met("-00:00:12") == -12
    assert parse_met("00:61:00") is None
    assert parse_met("garbage") is None
    assert parse_met(None) is None
    assert format_met(83.7) == "T+00:01:23"
    assert format_met(-12) == "T-00:00:12"


def test_hidden_panel_is_none_not_zero():
    sample = read_sample(record(10, sh=None, ss=None), 10.0)
    assert sample.sh_count is None and sample.ss_count is None
    # The stack's speed still counts as data for both vehicles before separation
    assert sample.sh_present and sample.ss_present
    sample = read_sample(record(200, sh=0, ss=6, sep=True, sh_speed=None), 200.0)
    assert sample.sh_count == 0 and sample.ss_count == 6
    gone = read_sample(record(200, sh=None, ss=6, sep=True, sh_speed=None), 200.0)
    assert not gone.sh_present and gone.ss_present


def test_ship_speed_in_mph_is_converted():
    payload = record(3000, ss=None, sep=True, ss_speed=1000.0)
    payload["speed_right"]["unit"] = "MPH"
    assert abs(read_sample(payload, 3000.0).ss_speed_kmh - 1609.344) < 1e-6


def test_met_filter_drops_a_misread_and_follows_a_real_jump():
    f = MetFilter()
    assert f.push(100, 130_000) == 100
    assert 100 < f.push(100, 130_500) < 101  # sub-second from the video clock
    assert f.push(800, 131_000) is None  # 08:00 misread
    assert f.push(101, 131_000) == 101
    # After a reconnect the clock jumps and stays there
    assert f.push(500, 140_000) is None
    assert f.push(501, 141_000) == 501


# --- Debouncing ---


def test_single_reading_never_changes_the_count():
    count = StableCount(TIMELINE_CONFIG)
    assert count.push(0.0, 33) is None
    assert count.push(1.0, 33) == (None, 33, 0.0)
    assert count.push(2.0, 12) is None  # phantom
    assert count.push(3.0, 33) is None
    assert count.push(4.0, None) is None  # panel hidden: no information
    assert count.value == 33


@given(st.lists(st.one_of(st.none(), st.integers(0, 33)), max_size=60))
@settings(max_examples=200, deadline=None)
def test_stable_count_only_takes_values_that_held(readings):
    count = StableCount(TIMELINE_CONFIG)
    visible: list[int] = []
    for i, reading in enumerate(readings):
        change = count.push(float(i), reading)
        if reading is not None:
            visible.append(reading)
        if change is not None:
            old, new, _ = change
            assert new == reading and new != old
            # The last confirm_records visible readings agree
            assert visible[-TIMELINE_CONFIG.confirm_records:] == [new] * TIMELINE_CONFIG.confirm_records


# --- Nominal flights ---


def test_nominal_v2_flight_confirms_every_rule():
    engine = run(nominal_flight(meco=3, boostback=13))
    assert statuses(engine) == {rule: "success" for rule in TIMELINE_RULES}
    meco = engine.verdict("sh_meco")
    assert meco.met == 160 and "33 -> 3 engines" in meco.evidence
    assert engine.verdict("ss_relight").met == 2400
    assert engine.verdict("sh_boostback_start").met == 170


def test_nominal_v3_flight_confirms_every_rule():
    engine = run(nominal_flight(meco=5, boostback=33))
    assert statuses(engine) == {rule: "success" for rule in TIMELINE_RULES}
    assert "33 -> 5 engines" in engine.verdict("sh_meco").evidence


def test_speed_thresholds_are_crossed_on_the_way_down():
    engine = run(nominal_flight())
    transonic, subsonic = engine.verdict("ss_transonic"), engine.verdict("ss_subsonic")
    assert transonic.met < subsonic.met
    assert "1235 km/h" in transonic.evidence


# --- Partial and failure ---


def _ascent_with(*segments: Segment):
    return flight([Segment(0, 60, sh=33, sh_speed=1500.0), *segments])


def test_engine_out_during_ascent_makes_meco_partial():
    engine = run(_ascent_with(Segment(60, 160, sh=32), Segment(160, 170, sh=3)))
    meco = engine.verdict("sh_meco")
    assert meco.status == "partial"
    assert "engine out at T+00:01:00 (33 -> 32" in meco.evidence


def test_meco_leaving_an_unplanned_count_is_partial():
    engine = run(_ascent_with(Segment(60, 160, sh=33), Segment(160, 170, sh=4)))
    assert engine.verdict("sh_meco").status == "partial"


def test_all_engines_off_long_before_meco_is_failure():
    engine = run(_ascent_with(Segment(60, 70, sh=0)))
    assert engine.verdict("sh_meco").status == "failure"


def test_all_engines_off_around_meco_is_not_decided():
    engine = run(_ascent_with(Segment(60, 155, sh=33), Segment(155, 170, sh=0)))
    assert engine.verdict("sh_meco") is None


def test_engine_out_mid_boostback_makes_its_end_partial():
    engine = run(flight([
        Segment(0, 160, sh=33),
        Segment(160, 170, sh=3),
        Segment(170, 190, sh=13, sep=True),
        Segment(190, 220, sh=12, sep=True),
        Segment(220, 230, sh=0, sep=True),
    ]))
    assert engine.verdict("sh_boostback_start").status == "success"
    assert engine.verdict("sh_boostback_end").status == "partial"


def test_landing_burn_cut_short_is_failure():
    engine = run(flight([
        Segment(0, 160, sh=33),
        Segment(160, 170, sh=3),
        Segment(170, 220, sh=13, sep=True),
        Segment(220, 380, sh=0, sep=True),
        Segment(380, 390, sh=13, sep=True),
        Segment(390, 400, sh=0, sep=True),
    ]))
    assert engine.verdict("sh_landing_start").status == "success"
    assert engine.verdict("sh_landing_end").status == "failure"


def _to_coast():
    return [
        Segment(0, 160, sh=33),
        Segment(160, 165, sh=3),
        Segment(165, 520, ss=6, sep=True),
        Segment(520, 600, ss=0, sep=True),
    ]


def test_relight_dying_after_three_seconds_is_failure():
    engine = run(flight([*_to_coast(), Segment(2400, 2403, ss=1, sep=True), Segment(2403, 2420, ss=0, sep=True)]))
    relight = engine.verdict("ss_relight")
    assert relight.status == "failure"
    assert "died after 3 s" in relight.evidence


def test_relight_never_lit_with_the_panel_visible_is_failure():
    engine = run(flight([*_to_coast(), Segment(2380, 2440, ss=0, sep=True)]))
    assert engine.verdict("ss_relight").status == "failure"
    assert "No relight" in engine.verdict("ss_relight").evidence


def test_relight_window_with_the_panel_hidden_decides_nothing():
    engine = run(flight([*_to_coast(), Segment(2380, 2440, ss=None, sep=True)]))
    assert engine.verdict("ss_relight") is None


def test_engine_out_during_ship_ascent_makes_seco_partial():
    engine = run(flight([
        Segment(0, 160, sh=33),
        Segment(160, 165, sh=3),
        Segment(165, 300, ss=6, sep=True),
        Segment(300, 520, ss=5, sep=True),
        Segment(520, 530, ss=0, sep=True),
    ]))
    assert engine.verdict("ss_seco").status == "partial"


def test_staggered_seco_is_still_success():
    engine = run(flight([
        Segment(0, 160, sh=33),
        Segment(160, 165, sh=3),
        Segment(165, 518, ss=6, sep=True),
        Segment(518, 521, ss=3, sep=True),
        Segment(521, 530, ss=0, sep=True),
    ]))
    assert engine.verdict("ss_seco").status == "success"


def test_ship_landing_burn_ending_on_three_fails_the_throttle_steps():
    engine = run(flight([*_to_coast(), Segment(3900, 3910, ss=3, sep=True), Segment(3910, 3920, ss=0, sep=True)]))
    assert engine.verdict("ss_landing_start").status == "success"
    assert engine.verdict("ss_landing_3_to_2").status == "failure"
    assert engine.verdict("ss_landing_2_to_1").status == "failure"


def test_ship_landing_burn_lighting_two_of_three_is_partial():
    engine = run(flight([*_to_coast(), Segment(3900, 3910, ss=2, sep=True)]))
    assert engine.verdict("ss_landing_start").status == "partial"


# --- Telemetry lost ---


def _booster_landing_then(*segments: Segment):
    return flight([
        Segment(0, 160, sh=33),
        Segment(160, 170, sh=3),
        Segment(170, 220, sh=13, sep=True),
        Segment(220, 380, sh=0, sep=True),
        Segment(380, 386, sh=13, sep=True),
        *segments,
    ])


def test_booster_lost_mid_landing_burn_fails_that_burn_only():
    engine = run(_booster_landing_then(Segment(386, 460, sh=None, sh_speed=None, ss=6, sep=True)))
    assert engine.verdict("sh_landing_end").status == "failure"
    assert "telemetry lost at T+00:06:25" in engine.verdict("sh_landing_end").evidence


def test_lost_failure_is_withdrawn_when_the_data_returns():
    engine = run(_booster_landing_then(
        Segment(386, 460, sh=None, sh_speed=None, ss=6, sep=True),
        Segment(460, 470, sh=None, sh_speed=500.0, ss=6, sep=True),
    ))
    assert engine.verdict("sh_landing_end") is None


def test_hidden_engine_panel_with_speed_showing_is_not_lost():
    engine = run(_booster_landing_then(Segment(386, 500, sh=None, sh_speed=500.0, ss=6, sep=True)))
    assert engine.verdict("sh_landing_end") is None


# --- Robustness ---

_samples = st.builds(
    RecordSample,
    met=st.floats(-100, 5000, allow_nan=False),
    sh_count=st.one_of(st.none(), st.integers(0, 33)),
    ss_count=st.one_of(st.none(), st.integers(0, 6)),
    sh_present=st.booleans(),
    ss_present=st.booleans(),
    ss_speed_kmh=st.one_of(st.none(), st.floats(0, 30000, allow_nan=False)),
    ss_altitude_km=st.one_of(st.none(), st.floats(0, 300, allow_nan=False)),
)


@given(st.lists(_samples, max_size=80))
@settings(max_examples=200, deadline=None)
def test_any_sample_sequence_gives_valid_verdicts(samples):
    engine = RuleEngine()
    engine.set_planned(PLANNED)
    for sample in sorted(samples, key=lambda s: s.met):
        engine.push(sample)
    for rule, verdict in engine.verdicts.items():
        assert rule in TIMELINE_RULES
        assert verdict.status in ("success", "partial", "failure")
        assert verdict.evidence


@given(st.lists(st.tuples(st.floats(0, 5000, allow_nan=False), st.booleans(), st.booleans()), max_size=80))
@settings(max_examples=200, deadline=None)
def test_hidden_panels_are_never_evidence(rows):
    """With no engine panel ever on screen, nothing but liftoff is decided, and nothing fails."""
    engine = RuleEngine()
    engine.set_planned(PLANNED)
    for met, sh_present, ss_present in sorted(rows):
        engine.push(RecordSample(met, None, None, sh_present, ss_present))
    assert set(engine.verdicts) <= {"liftoff"}
