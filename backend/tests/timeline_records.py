"""Synthetic telemetry records and flights for the timeline status tests.

``record()`` builds one broadcast ``telemetry`` payload; ``flight()`` turns a
list of segments (MET range and what each vehicle shows) into one record per
second. A count of None means that vehicle's engine panel is not on screen.
"""

from dataclasses import dataclass
from typing import Any

from src.storage.missions import TimelineEvent

SH_ENGINES = [f"sh_e{n}" for n in range(1, 34)]
SS_ENGINES = [f"ss_e{n}" for n in range(1, 7)]


def _engines(ids: list[str], running: int | None) -> dict[str, str]:
    if running is None:
        return {e: "undetected" for e in ids}
    return {e: "active" if i < running else "inactive" for i, e in enumerate(ids)}


def _field(value: float | None, unit: str) -> dict[str, Any]:
    if value is None:
        return {"value": None, "unit": None, "status": "unavailable"}
    return {"value": value, "unit": unit, "status": "available"}


def met_text(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def record(
    met: int,
    sh: int | None = None,
    ss: int | None = None,
    *,
    sep: bool = False,
    sh_speed: float | None = 1000.0,
    ss_speed: float | None = 1000.0,
    ss_alt: float | None = 100.0,
    timestamp: int | None = None,
    met_text_override: str | None = None,
) -> dict[str, Any]:
    """One telemetry payload at whole-second ``met``.

    Before separation both sides show the stack (sh_speed); after it the left
    side is Super Heavy and the right Starship. A speed of None means that
    side shows nothing.
    """
    if sep:
        left, right = _field(sh_speed, "KM/H"), _field(ss_speed, "KM/H")
        left_alt, right_alt = _field(10.0 if sh_speed is not None else None, "KM"), _field(ss_alt if ss_speed is not None else None, "KM")
    else:
        left = right = _field(sh_speed, "KM/H")
        left_alt = right_alt = _field(10.0 if sh_speed is not None else None, "KM")
    return {
        "sequence_number": met + 1,
        "frame_number": met * 30,
        "mission_elapsed_time": met_text_override or met_text(met),
        "mission_elapsed_time_raw": None,
        "speed_left": left,
        "speed_right": right,
        "altitude_left": left_alt,
        "altitude_right": right_alt,
        "stage_left_label": None,
        "stage_right_label": None,
        "stage_separation_text": None,
        "stage_assignment_left": "super_heavy",
        "stage_assignment_right": "starship" if sep else "super_heavy",
        "separation_state": "post_separation" if sep else "pre_separation",
        "t_zero_found": True,
        "stage_sep_found": sep,
        "starship_engines": _engines(SS_ENGINES, ss),
        "superheavy_engines": _engines(SH_ENGINES, sh),
        "detection_accuracy": {"starship": 1.0, "superheavy": 1.0},
        "timestamp": (met + 30) * 1000 if timestamp is None else timestamp,
    }


@dataclass
class Segment:
    """What the HUD shows from ``start`` up to (not including) ``end``."""

    start: int
    end: int
    sh: int | None = None
    ss: int | None = None
    sep: bool = False
    sh_speed: float | None = 1000.0
    ss_speed: float | None = 1000.0
    # Ship speed falls linearly from ss_speed to this over the segment
    ss_speed_to: float | None = None


def flight(segments: list[Segment]) -> list[dict[str, Any]]:
    records = []
    for seg in segments:
        for met in range(seg.start, seg.end):
            speed = seg.ss_speed
            if seg.ss_speed_to is not None and speed is not None:
                speed = speed + (seg.ss_speed_to - speed) * (met - seg.start) / max(1, seg.end - seg.start - 1)
            records.append(record(met, seg.sh, seg.ss, sep=seg.sep, sh_speed=seg.sh_speed, ss_speed=speed))
    return records


def nominal_flight(meco: int = 3, boostback: int = 13) -> list[dict[str, Any]]:
    """A whole flight that goes to plan: V2 (MECO 3, boostback 13) or V3 (5, 33)."""
    return flight([
        Segment(0, 160, sh=33, ss_speed=None, sh_speed=1500.0),
        Segment(160, 165, sh=meco, sh_speed=5000.0),
        Segment(165, 170, sh=meco, ss=6, sep=True),
        Segment(170, 220, sh=boostback, ss=6, sep=True, ss_speed=8000.0),
        Segment(220, 380, sh=0, ss=6, sep=True, ss_speed=12000.0),
        Segment(380, 386, sh=13, ss=6, sep=True, ss_speed=20000.0),
        Segment(386, 391, sh=5, ss=6, sep=True, ss_speed=20000.0),
        Segment(391, 401, sh=3, ss=6, sep=True, ss_speed=20000.0),
        Segment(401, 410, sh=0, ss=6, sep=True, ss_speed=24000.0),
        Segment(410, 520, sh=None, ss=6, sep=True, sh_speed=None, ss_speed=26000.0),
        Segment(520, 600, ss=0, sep=True, sh_speed=None, ss_speed=26500.0),
        Segment(600, 2400, ss=None, sep=True, sh_speed=None, ss_speed=26500.0),
        Segment(2400, 2410, ss=1, sep=True, sh_speed=None, ss_speed=26500.0),
        Segment(2410, 2420, ss=0, sep=True, sh_speed=None, ss_speed=26500.0),
        Segment(2420, 3800, ss=None, sep=True, sh_speed=None, ss_speed=26500.0, ss_speed_to=900.0),
        Segment(3800, 3900, ss=0, sep=True, sh_speed=None, ss_speed=300.0),
        Segment(3900, 3906, ss=3, sep=True, sh_speed=None, ss_speed=200.0),
        Segment(3906, 3911, ss=2, sep=True, sh_speed=None, ss_speed=100.0),
        Segment(3911, 3921, ss=1, sep=True, sh_speed=None, ss_speed=20.0),
        Segment(3921, 3930, ss=0, sep=True, sh_speed=None, ss_speed=0.0),
    ])


# Planned times of the rule events in nominal_flight
PLANNED = {
    "liftoff": 0,
    "sh_meco": 160,
    "sh_boostback_start": 170,
    "sh_boostback_end": 220,
    "sh_landing_start": 380,
    "sh_landing_end": 400,
    "ss_seco": 520,
    "ss_relight": 2400,
    "ss_transonic": 3700,
    "ss_subsonic": 3720,
    "ss_landing_start": 3900,
    "ss_landing_3_to_2": 3906,
    "ss_landing_2_to_1": 3911,
}


def rule_timeline(planned: dict[str, int] = PLANNED) -> list[TimelineEvent]:
    """One rule event per planned time, ids are the rule names."""
    return [
        TimelineEvent(id=rule.replace("_", "-"), t=t, event=rule, vehicle="both", auto={"kind": "rule", "rule": rule})
        for rule, t in planned.items()
    ]


def event_id(rule: str) -> str:
    return rule.replace("_", "-")
