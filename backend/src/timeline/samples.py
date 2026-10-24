"""What the timeline rules read from one telemetry record.

A record is the ``telemetry`` payload the pipeline broadcasts (a
``TelemetryRecord`` dump). Each is reduced to a ``RecordSample``: its MET in
seconds, each vehicle's running engine count (None while that vehicle's
engine panel is not on screen, which is no evidence of anything), whether
the vehicle has any reading at all, and the ship's speed and altitude.
"""

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.timeline.config import TIMELINE_CONFIG, TimelineConfig

_MET = re.compile(r"^\s*T?\s*([+-])?\s*(\d{1,2}):(\d{2}):(\d{2})\s*$")

_SPEED_TO_KMH = {"KM/H": 1.0, "MPH": 1.609344}
_ALTITUDE_TO_KM = {"KM": 1.0, "MI": 1.609344, "FT": 0.0003048}

# stage_assignment_* values (src.telemetry.stage_assignment)
_STARSHIP = "starship"
_SUPER_HEAVY = "super_heavy"


@dataclass(frozen=True)
class RecordSample:
    """One record as the rules see it."""

    met: float
    # Engines reading 'active', or None while no engine of the vehicle is on screen
    sh_count: int | None
    ss_count: int | None
    # Any engine, speed or altitude reading of the vehicle
    sh_present: bool
    ss_present: bool
    ss_speed_kmh: float | None = None
    ss_altitude_km: float | None = None


def parse_met(text: str | None) -> int | None:
    """``"00:01:23"`` (optionally ``T+``/``-`` signed) -> 83 seconds; None if unreadable."""
    if not text:
        return None
    match = _MET.match(text)
    if not match:
        return None
    sign, hours, minutes, seconds = match.groups()
    if int(minutes) >= 60 or int(seconds) >= 60:
        return None
    total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    return -total if sign == "-" else total


def format_met(seconds: float) -> str:
    """83 -> ``"T+00:01:23"``, -12 -> ``"T-00:00:12"`` (whole seconds, rounded down in size)."""
    sign = "-" if seconds < 0 else "+"
    whole = int(abs(seconds))
    return f"T{sign}{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}"


def _engine_count(engines: Mapping[str, str] | None) -> int | None:
    """Engines running, or None when the panel is not on screen (nothing read)."""
    if not engines:
        return None
    statuses = list(engines.values())
    if not any(s in ("active", "inactive") for s in statuses):
        return None
    return sum(1 for s in statuses if s == "active")


def _reading(field: Any, factors: Mapping[str, float], default_unit: str) -> float | None:
    """A speed or altitude reading in metric units, or None when it was not read."""
    if not isinstance(field, Mapping) or field.get("status") != "available":
        return None
    value = field.get("value")
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    factor = factors.get(str(field.get("unit") or default_unit).upper())
    return None if factor is None else float(value) * factor


def _sides(record: Mapping[str, Any], stage: str) -> tuple[str, ...]:
    """The HUD sides showing a vehicle: both (the stack) before separation."""
    if record.get("separation_state") != "post_separation":
        return ("left", "right")
    return tuple(s for s in ("left", "right") if record.get(f"stage_assignment_{s}") == stage)


def _vehicle_value(
    record: Mapping[str, Any], stage: str, quantity: str, factors: Mapping[str, float], unit: str
) -> float | None:
    for side in _sides(record, stage):
        value = _reading(record.get(f"{quantity}_{side}"), factors, unit)
        if value is not None:
            return value
    return None


def _present(record: Mapping[str, Any], stage: str, count: int | None) -> bool:
    if count is not None:
        return True
    return any(
        _vehicle_value(record, stage, quantity, factors, unit) is not None
        for quantity, factors, unit in (("speed", _SPEED_TO_KMH, "KM/H"), ("altitude", _ALTITUDE_TO_KM, "KM"))
    )


def read_sample(record: Mapping[str, Any], met: float) -> RecordSample:
    """The rules' view of a record whose MET (seconds) is already known."""
    sh_count = _engine_count(record.get("superheavy_engines"))
    ss_count = _engine_count(record.get("starship_engines"))
    return RecordSample(
        met=met,
        sh_count=sh_count,
        ss_count=ss_count,
        sh_present=_present(record, _SUPER_HEAVY, sh_count),
        ss_present=_present(record, _STARSHIP, ss_count),
        ss_speed_kmh=_vehicle_value(record, _STARSHIP, "speed", _SPEED_TO_KMH, "KM/H"),
        ss_altitude_km=(
            _vehicle_value(record, _STARSHIP, "altitude", _ALTITUDE_TO_KM, "KM")
            if record.get("separation_state") == "post_separation"
            else None
        ),
    )


class MetFilter:
    """Turns each record's whole-second MET into a checked, sub-second MET.

    The HUD clock only shows whole seconds, so the fraction comes from the
    frame's position in the video (``timestamp``, ms) since the clock last
    ticked. A reading far from what the video clock predicts is an OCR
    misread and is dropped, unless ``confirm_records`` consecutive readings
    agree with each other (a real jump, e.g. after the stream reconnected).
    """

    def __init__(self, config: TimelineConfig = TIMELINE_CONFIG) -> None:
        self._config = config
        self.reset()

    def reset(self) -> None:
        # (whole-second MET, timestamp in ms when it first showed)
        self._anchor: tuple[int, float | None] | None = None
        self._outliers: list[tuple[int, float | None]] = []

    def push(self, met: int, timestamp_ms: float | None) -> float | None:
        """The record's MET in seconds, or None if the reading was dropped."""
        if self._anchor is None:
            return self._accept(met, timestamp_ms)
        expected = self._predict(self._anchor, timestamp_ms)
        if abs(met - expected) <= self._config.met_tolerance_s + 1:
            self._outliers.clear()
            if met != self._anchor[0]:
                return self._accept(met, timestamp_ms)
            return met + self._fraction(timestamp_ms)
        if self._outliers and abs(met - self._predict(self._outliers[-1], timestamp_ms)) <= (
            self._config.met_tolerance_s + 1
        ):
            self._outliers.append((met, timestamp_ms))
        else:
            self._outliers = [(met, timestamp_ms)]
        if len(self._outliers) >= self._config.confirm_records:
            return self._accept(met, timestamp_ms)
        return None

    def _accept(self, met: int, timestamp_ms: float | None) -> float:
        self._anchor = (met, timestamp_ms)
        self._outliers = []
        return float(met)

    @staticmethod
    def _predict(anchor: tuple[int, float | None], timestamp_ms: float | None) -> float:
        met, at = anchor
        if at is None or timestamp_ms is None:
            return float(met)
        return met + (timestamp_ms - at) / 1000

    def _fraction(self, timestamp_ms: float | None) -> float:
        assert self._anchor is not None
        at = self._anchor[1]
        if at is None or timestamp_ms is None:
            return 0.0
        return min(0.999, max(0.0, (timestamp_ms - at) / 1000))
