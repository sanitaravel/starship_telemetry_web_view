"""Tests for the timeline part of the mission files."""

import json
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st
from pydantic import ValidationError

from src.storage.missions import (
    MAX_TIMELINE_EVENTS,
    MAX_TIMELINE_SECONDS,
    TIMELINE_RULES,
    MissionSettings,
    list_missions,
    load_mission,
    save_mission,
)


def event(i: int, t: int = 0, text: str = "Liftoff", vehicle: str = "both", auto: dict | None = None) -> dict:
    return {"id": f"e{i}", "t": t, "event": text, "vehicle": vehicle, "auto": auto or {"kind": "manual"}}


autos = st.one_of(
    st.just({"kind": "manual"}),
    st.just({"kind": "clock"}),
    st.text(alphabet="ABCDEF GHIJ", min_size=1, max_size=40)
    .filter(lambda s: s.strip())
    .map(lambda s: {"kind": "hud", "label": s.strip()}),
    st.sampled_from(TIMELINE_RULES).map(lambda r: {"kind": "rule", "rule": r}),
)
event_texts = st.text(alphabet="abc XYZ()-", min_size=1, max_size=200).filter(lambda s: s.strip()).map(str.strip)
timelines = st.lists(
    st.tuples(
        st.integers(-MAX_TIMELINE_SECONDS, MAX_TIMELINE_SECONDS),
        event_texts,
        st.sampled_from(["starship", "superheavy", "both"]),
        autos,
    ),
    max_size=40,
).map(lambda rows: [event(i, t, text, v, a) for i, (t, text, v, a) in enumerate(rows)])


class TestValidation:
    def test_rule_ids_are_the_agreed_ones(self):
        assert len(TIMELINE_RULES) == 13 and len(set(TIMELINE_RULES)) == 13
        assert "liftoff" in TIMELINE_RULES and "ss_subsonic" in TIMELINE_RULES

    def test_default_is_empty(self):
        assert MissionSettings(mission="IFT 14").timeline == []

    @pytest.mark.parametrize(
        "bad",
        [
            event(1, text=""),
            event(1, text="   "),
            event(1, text="x" * 201),
            event(1, vehicle="booster"),
            event(1, t=MAX_TIMELINE_SECONDS + 1),
            event(1, t=-MAX_TIMELINE_SECONDS - 1),
            event(1, auto={"kind": "rule", "rule": "warp_drive"}),
            event(1, auto={"kind": "hud", "label": ""}),
            event(1, auto={"kind": "hud", "label": "  "}),
            event(1, auto={"kind": "hud"}),
            event(1, auto={"kind": "magic"}),
            event(1, auto={"kind": "manual", "extra": 1}),
            {**event(1), "id": ""},
            {**event(1), "id": "a b"},
            {**event(1), "id": "x" * 41},
            {**event(1), "t": 1.5},
        ],
    )
    def test_rejects_invalid_event(self, bad: dict):
        with pytest.raises(ValidationError):
            MissionSettings(mission="IFT 14", timeline=[bad])

    def test_rejects_duplicate_ids(self):
        with pytest.raises(ValidationError):
            MissionSettings(mission="IFT 14", timeline=[event(1, 0), event(1, 5)])

    def test_caps_the_number_of_events(self):
        MissionSettings(mission="IFT 14", timeline=[event(i, i) for i in range(MAX_TIMELINE_EVENTS)])
        with pytest.raises(ValidationError):
            MissionSettings(mission="IFT 14", timeline=[event(i, i) for i in range(MAX_TIMELINE_EVENTS + 1)])

    def test_strips_text_and_accepts_the_bounds(self):
        parsed = MissionSettings(
            mission="IFT 14",
            timeline=[event(1, MAX_TIMELINE_SECONDS, "  Landing  "), event(2, -MAX_TIMELINE_SECONDS)],
        )
        assert [e.t for e in parsed.timeline] == [-MAX_TIMELINE_SECONDS, MAX_TIMELINE_SECONDS]
        assert parsed.timeline[1].event == "Landing"

    def test_sorts_by_time_and_keeps_both_t0_rows_in_order(self):
        parsed = MissionSettings(
            mission="IFT 14",
            timeline=[
                event(1, 58, "Max Q"),
                event(2, 0, "Excitement guaranteed"),
                event(3, -30, "GO for launch"),
                event(4, 0, "Liftoff"),
            ],
        )
        assert [e.event for e in parsed.timeline] == ["GO for launch", "Excitement guaranteed", "Liftoff", "Max Q"]


class TestFiles:
    def test_roundtrip_through_the_file(self, tmp_path: Path):
        rows = [
            event(1, -30, "GO", "both", {"kind": "clock"}),
            event(2, 0, "Liftoff", "both", {"kind": "rule", "rule": "liftoff"}),
            event(3, 58, "Max Q", "both", {"kind": "hud", "label": "MAX Q"}),
        ]
        save_mission(MissionSettings(mission="IFT 14", timeline=rows), tmp_path)
        assert load_mission("ift-14", tmp_path)["timeline"] == rows

    def test_old_files_without_a_timeline_still_load(self, tmp_path: Path):
        (tmp_path / "engines_ift-13.json").write_text(
            json.dumps({"mission": "IFT 13", "ship": "S39", "updated_at": "2026-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        assert load_mission("ift-13", tmp_path)["timeline"] == []
        assert [m["id"] for m in list_missions(tmp_path)] == ["ift-13"]

    def test_listing_leaves_the_timeline_out(self, tmp_path: Path):
        save_mission(MissionSettings(mission="IFT 14", timeline=[event(1)]), tmp_path)
        assert "timeline" not in list_missions(tmp_path)[0]

    @settings(max_examples=40, deadline=None)
    @given(rows=timelines)
    def test_roundtrip_sorts_stably(self, rows: list[dict]):
        parsed = MissionSettings(mission="IFT 14", timeline=rows)
        expected = sorted(rows, key=lambda r: r["t"])  # Python's sort is stable
        assert [e.model_dump() for e in parsed.timeline] == expected
        again = MissionSettings.model_validate(parsed.model_dump())
        assert again == parsed
