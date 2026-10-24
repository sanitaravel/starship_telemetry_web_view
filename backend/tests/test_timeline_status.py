"""The timeline status machine (src/timeline/status.py)."""

import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule

from src.storage.missions import TIMELINE_RULES, TimelineEvent
from src.timeline.status import (
    STATUSES,
    TimelineStatusMachine,
    labels_match,
    normalize_label,
    plausible_label,
)
from tests.timeline_records import PLANNED, Segment, event_id, flight, nominal_flight, record, rule_timeline


def fake_clock():
    ticks = itertools.count()
    return lambda: f"2026-10-24T00:00:{next(ticks):06d}Z"


def machine(events=(), **kwargs) -> TimelineStatusMachine:
    m = TimelineStatusMachine(now=fake_clock(), **kwargs)
    m.set_timeline(events)
    return m


def ev(id: str, t: int, auto: dict, event: str = "Event") -> TimelineEvent:
    return TimelineEvent(id=id, t=t, event=event, vehicle="both", auto=auto)


def status_of(m: TimelineStatusMachine, event: str) -> str:
    return m.status(event).status


# --- Per kind ---


def test_everything_is_not_yet_before_any_met():
    m = machine([ev("a", -60, {"kind": "clock"}), ev("b", 10, {"kind": "manual"})])
    assert [s.status for s in m.snapshot()] == ["not_yet", "not_yet"]
    assert all(s.source == "auto" for s in m.snapshot())


def test_manual_becomes_pending_at_its_time():
    m = machine([ev("a", 10, {"kind": "manual"})])
    m.on_met(9.5)
    assert status_of(m, "a") == "not_yet"
    change = m.on_met(10)
    assert [s.event_id for s in change.changed] == ["a"]
    assert status_of(m, "a") == "pending"


def test_clock_succeeds_at_its_time_also_in_the_countdown():
    m = machine([ev("a", -40, {"kind": "clock"})])
    m.on_met(-41, "countdown")
    assert status_of(m, "a") == "not_yet"
    m.on_met(-40, "countdown")
    status = m.status("a")
    assert (status.status, status.evidence, status.met) == ("success", "Clock reached T-00:00:40", -40)
    assert m.clock_source == "countdown"


def test_lower_met_never_takes_a_status_back():
    m = machine([ev("a", 10, {"kind": "clock"})])
    m.on_met(20)
    assert not m.on_met(5)
    assert status_of(m, "a") == "success"
    assert m.met == 20


def test_unknown_clock_source_is_rejected():
    with pytest.raises(ValueError):
        machine().on_met(1, "sundial")


def test_hud_label_confirms_and_clipped_labels_match():
    m = machine([ev("land", 400, {"kind": "hud", "label": "SUPER HEAVY LANDING"})])
    m.on_met(405)
    m.on_hud_label("Super Heavy Land", 405)
    status = m.status("land")
    assert (status.status, status.evidence, status.met) == ("success", "HUD milestone SUPER HEAVY LAND", 405)


def test_hud_without_label_is_pending_after_the_grace_window():
    m = machine([ev("maxq", 60, {"kind": "hud", "label": "MAX Q"})])
    m.on_met(89)
    assert status_of(m, "maxq") == "not_yet"
    m.on_met(90)
    assert status_of(m, "maxq") == "pending"
    m.on_hud_label("MAX Q", 95)  # late label still wins
    assert status_of(m, "maxq") == "success"


def test_a_label_scrolling_past_confirms_one_event_and_the_closest():
    m = machine([
        ev("burn1", 100, {"kind": "hud", "label": "LANDING BURN"}),
        ev("burn2", 3900, {"kind": "hud", "label": "LANDING BURN"}),
    ])
    m.on_hud_label("LANDING BURN", 3905)
    m.on_hud_label("LANDING BURN", 3906)  # same sighting
    assert status_of(m, "burn2") == "success"
    assert status_of(m, "burn1") == "not_yet"


def test_hud_label_far_from_the_plan_does_not_match():
    m = machine([ev("sep", 160, {"kind": "hud", "label": "STAGE SEP"})])
    m.on_hud_label("STAGE SEP", 2000)
    assert status_of(m, "sep") == "not_yet"


@pytest.mark.parametrize(
    "read, label, expected",
    [
        ("SUPERHEAVYLAND", "SUPERHEAVYLANDING", True),
        ("UPERHEAVYLANDIN", "SUPERHEAVYLANDING", True),
        ("MAXQ", "MAXQ", True),
        ("MAX", "MAXQ", False),  # too short to be a clipped label
        ("SECO", "STAGESEP", False),
        ("", "MAXQ", False),
    ],
)
def test_labels_match(read, label, expected):
    assert labels_match(read, label, 5) is expected


@given(st.text(max_size=30), st.text(max_size=30))
def test_label_matching_is_symmetric_and_reflexive(a, b):
    na, nb = normalize_label(a), normalize_label(b)
    assert labels_match(na, nb, 5) == labels_match(nb, na, 5)
    if na:
        assert labels_match(na, na, 5)


def test_rule_without_evidence_is_pending_after_grace():
    m = machine([ev("meco", 160, {"kind": "rule", "rule": "sh_meco"})])
    m.on_met(189)
    assert status_of(m, "meco") == "not_yet"
    m.on_met(190)
    assert m.status("meco").status == "pending"
    assert m.status("meco").evidence == "No telemetry evidence by T+00:03:10"


def test_nominal_flight_records_confirm_every_rule_event():
    m = machine(rule_timeline())
    for payload in nominal_flight():
        m.on_record(payload)
    assert {s.event_id: s.status for s in m.snapshot()} == {event_id(r): "success" for r in PLANNED}
    assert m.met is not None and m.met >= 3929


def test_hidden_booster_panel_leaves_its_burns_pending():
    """After separation the booster's engine icons are gone: its burns need a person."""
    m = machine(rule_timeline())
    for payload in flight([
        Segment(0, 160, sh=33),
        Segment(160, 165, sh=3),
        Segment(165, 520, sh=None, ss=6, sep=True),
    ]):
        m.on_record(payload)
    for rule in ("sh_boostback_start", "sh_boostback_end", "sh_landing_start", "sh_landing_end"):
        assert status_of(m, event_id(rule)) == "pending", rule
    assert status_of(m, "sh-meco") == "success"


def test_misread_met_does_not_jump_the_clock():
    m = machine([ev("late", 3000, {"kind": "clock"})])
    m.on_record(record(10, sh=33))
    m.on_record(record(11, sh=33, met_text_override="08:00:11", timestamp=41_000))
    m.on_record(record(12, sh=33))
    assert status_of(m, "late") == "not_yet"
    assert m.met < 20


# --- Overrides ---


def test_human_override_sticks_across_later_evidence_and_back_to_auto_recomputes():
    m = machine(rule_timeline())
    m.override("sh-meco", "failure")
    for payload in nominal_flight():
        m.on_record(payload)
    status = m.status("sh-meco")
    assert (status.status, status.source) == ("failure", "human")
    assert "auto: success" in status.evidence
    change = m.override("sh-meco", None)
    assert [s.event_id for s in change.changed] == ["sh-meco"]
    status = m.status("sh-meco")
    assert (status.status, status.source) == ("success", "auto")


def test_skipped_is_set_by_a_person_only():
    m = machine([ev("a", 10, {"kind": "manual"})])
    m.override("a", "skipped")
    assert m.status("a").status == "skipped" and m.status("a").source == "human"


def test_override_of_unknown_event_or_status_is_rejected():
    m = machine([ev("a", 10, {"kind": "manual"})])
    with pytest.raises(KeyError):
        m.override("nope", "success")
    with pytest.raises(ValueError):
        m.override("a", "great")


def test_reset_clears_the_run_but_keeps_the_timeline():
    m = machine([ev("a", 10, {"kind": "clock"}), ev("b", 20, {"kind": "manual"})])
    m.on_met(30)
    m.override("b", "success")
    change = m.reset()
    assert {s.event_id for s in change.changed} == {"a", "b"}
    assert [(s.status, s.source) for s in m.snapshot()] == [("not_yet", "auto")] * 2
    assert m.met is None


# --- Timeline changes ---


def test_statuses_survive_a_timeline_change_for_ids_that_stay():
    m = machine([ev("a", 10, {"kind": "manual"}), ev("b", 20, {"kind": "manual"})])
    m.on_met(15)
    m.override("a", "success")
    before = m.status("a")
    change = m.set_timeline([ev("a", 10, {"kind": "manual"}), ev("c", 12, {"kind": "clock"})])
    assert change.removed == ("b",)
    assert [s.event_id for s in change.changed] == ["c"]
    assert m.status("a") == before
    assert [s.event_id for s in m.snapshot()] == ["a", "c"]


def test_hud_labels_seen_before_an_event_was_added_still_count():
    m = machine([])
    m.on_hud_label("MAX Q", 65)
    m.set_timeline([ev("maxq", 60, {"kind": "hud", "label": "MAX Q"})])
    assert status_of(m, "maxq") == "success"


def test_duplicate_ids_are_rejected():
    with pytest.raises(ValueError):
        machine([ev("a", 1, {"kind": "manual"}), ev("a", 2, {"kind": "manual"})])


# --- Stateful ---

_AUTOS = st.one_of(
    st.just({"kind": "manual"}),
    st.just({"kind": "clock"}),
    st.sampled_from(["MAX Q", "STAGE SEP", "SUPER HEAVY LANDING"]).map(lambda l: {"kind": "hud", "label": l}),
    st.sampled_from(TIMELINE_RULES).map(lambda r: {"kind": "rule", "rule": r}),
)
_IDS = [f"e{n}" for n in range(6)]
_timelines = st.lists(
    st.tuples(st.sampled_from(_IDS), st.integers(-120, 600), _AUTOS),
    max_size=6,
    unique_by=lambda row: row[0],
).map(lambda rows: [ev(i, t, auto) for i, t, auto in rows])


class TimelineStatusModel(RuleBasedStateMachine):
    """Random timelines, clocks, records, labels and overrides; the machine's
    statuses always follow the rules of status.py."""

    @initialize(events=_timelines)
    def setup(self, events):
        self.m = TimelineStatusMachine(now=fake_clock())
        self.overrides: dict[str, str] = {}
        self.last = {}
        self._apply(self.m.set_timeline(events))

    def _apply(self, change):
        """The change set names exactly the events whose status tuple changed."""
        now = {s.event_id: s for s in self.m.snapshot()}
        changed = {s.event_id for s in change.changed}
        for event_id, status in now.items():
            before = self.last.get(event_id)
            differs = before is None or (before.status, before.source, before.evidence, before.met) != (
                status.status, status.source, status.evidence, status.met
            )
            assert differs == (event_id in changed), event_id
            if event_id not in changed:
                assert status == before
        assert set(change.removed) == set(self.last) - set(now)
        self.last = now

    @rule(events=_timelines)
    def set_timeline(self, events):
        kept = {e.id for e in events}
        self.overrides = {k: v for k, v in self.overrides.items() if k in kept}
        self._apply(self.m.set_timeline(events))

    @rule(met=st.floats(-200, 700, allow_nan=False), countdown=st.booleans())
    def on_met(self, met, countdown):
        self._apply(self.m.on_met(met, "countdown" if countdown else "telemetry"))

    @rule(met=st.integers(0, 700), sh=st.one_of(st.none(), st.integers(0, 33)), ss=st.one_of(st.none(), st.integers(0, 6)))
    def on_record(self, met, sh, ss):
        self._apply(self.m.on_record(record(met, sh, ss, sep=met > 165)))

    @rule(label=st.sampled_from(["MAX Q", "STAGE SEP", "SUPER HEAVY LAND", "SECO", "x"]), met=st.integers(-100, 700))
    def on_hud_label(self, label, met):
        self._apply(self.m.on_hud_label(label, met))

    @precondition(lambda self: bool(self.m.events))
    @rule(data=st.data(), status=st.one_of(st.none(), st.sampled_from(STATUSES)))
    def override(self, data, status):
        event_id = data.draw(st.sampled_from([e.id for e in self.m.events]))
        if status is None:
            self.overrides.pop(event_id, None)
        else:
            self.overrides[event_id] = status
        self._apply(self.m.override(event_id, status))

    @rule()
    def reset(self):
        self.overrides = {}
        self._apply(self.m.reset())

    @invariant()
    def snapshot_follows_the_timeline(self):
        assert [s.event_id for s in self.m.snapshot()] == [e.id for e in self.m.events]

    @invariant()
    def overrides_win_and_auto_never_skips(self):
        for status in self.m.snapshot():
            assert status.status in STATUSES
            if status.event_id in self.overrides:
                assert (status.status, status.source) == (self.overrides[status.event_id], "human")
            else:
                assert status.source == "auto"
                assert status.status != "skipped"

    @invariant()
    def time_based_statuses_follow_the_clock(self):
        met = self.m.met
        for event in self.m.events:
            status = self.m.status(event.id)
            if status.source == "human":
                continue
            kind = event.auto.kind
            if met is None:
                assert status.status in ("not_yet", "success") if kind == "hud" else status.status in (
                    "not_yet", "success", "partial", "failure"
                )
                continue
            if kind == "manual":
                assert status.status == ("pending" if met >= event.t else "not_yet")
            elif kind == "clock":
                assert status.status == ("success" if met >= event.t else "not_yet")
            elif status.status == "pending":
                assert met >= event.t + 30
            elif status.status == "not_yet":
                assert met < event.t + 30


TestTimelineStatusModel = TimelineStatusModel.TestCase
TestTimelineStatusModel.settings = settings(max_examples=150, stateful_step_count=30, deadline=None)


# --- HUD reads off the real video: look-alikes, noise, evidence ---


@pytest.mark.parametrize(
    "read, label, expected",
    [
        ("MAX0", "MAXQ", True),  # Q read as 0
        ("SEC0", "SECO", True),
        ("TAGESEP", "STAGESEP", True),  # clipped on the left
        ("MAX", "MAXQ", False),
    ],
)
def test_labels_match_folds_look_alikes(read, label, expected):
    assert labels_match(read, label, 5) is expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("MAX 0", True),
        ("STAGE SE", True),
        ("HEAVY LANC", True),
        ("3", False),
        ("1 _", False),
        ("4ad", False),
        ("12:31", False),
    ],
)
def test_plausible_label(text, expected):
    assert plausible_label(text) is expected


def test_label_read_with_look_alikes_names_the_milestone():
    m = machine([ev("maxq", 58, {"kind": "hud", "label": "MAX Q"})])
    m.on_met(62)
    m.on_hud_label("MAX 0", 62)
    assert m.status("maxq").evidence == 'HUD milestone MAX Q (read "MAX 0")'


def test_matches_hud_event():
    m = machine([ev("sep", 141, {"kind": "hud", "label": "STAGE SEP"})])
    assert m.matches_hud_event("TAGE SEP")
    assert not m.matches_hud_event("SECO")
