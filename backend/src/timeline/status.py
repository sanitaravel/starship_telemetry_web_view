"""Each timeline event's live status, from the clock, HUD labels, telemetry rules and people.

Pure logic, no I/O. ``TimelineStatusMachine`` keeps the facts of the run (the
latest MET, the HUD milestone labels read, the rules' verdicts, human
overrides) and derives every event's status from them, so changing the
timeline or clearing an override simply recomputes:

- An override by a person always wins until it is cleared ("back to auto").
  ``skipped`` is only ever set by a person.
- Otherwise the event's ``auto`` source decides:
  ``manual``: not_yet, then pending (needs a person) once MET reaches ``t``.
  ``clock``: success once MET reaches ``t``.
  ``hud``: success when a HUD milestone label matching it is read; pending
  if none was read by ``t + grace_s``.
  ``rule``: the rule's verdict (success, partial or failure; see
  ``rules.py``); pending if it has none by ``t + grace_s``.

Every input returns a ``ChangeSet``: the events whose status, source,
evidence or MET changed (each with a fresh ``updated_at``), empty when
nothing did.
"""

import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Literal, Mapping

from src.storage.missions import TimelineEvent
from src.timeline.config import TIMELINE_CONFIG, TimelineConfig
from src.timeline.rules import RuleEngine
from src.timeline.samples import MetFilter, format_met, parse_met, read_sample

Status = Literal["not_yet", "pending", "success", "partial", "failure", "skipped"]
STATUSES: tuple[str, ...] = ("not_yet", "pending", "success", "partial", "failure", "skipped")
Source = Literal["auto", "human"]
# Where the MET came from: the telemetry records' HUD clock, or (phase D) the
# countdown clock before T-0
ClockSource = Literal["telemetry", "countdown"]
CLOCK_SOURCES: tuple[str, ...] = ("telemetry", "countdown")


def utc_now() -> str:
    """Now as an ISO 8601 UTC timestamp, the ``updated_at`` of a status."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class EventStatus:
    """One event's status as the API sends it."""

    event_id: str
    status: Status
    source: Source
    evidence: str | None
    # MET in seconds when it was decided (None when no MET is known)
    met: float | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChangeSet:
    """What one input changed: events with a new status, and ids no longer in the timeline."""

    changed: tuple[EventStatus, ...] = ()
    removed: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.changed or self.removed)


@dataclass(frozen=True)
class _Decision:
    status: Status
    source: Source
    evidence: str | None
    met: float | None


def normalize_label(text: str) -> str:
    """HUD label for matching: upper case, letters and digits only ("Super heavy land" -> "SUPERHEAVYLAND")."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def labels_match(read: str, label: str, min_chars: int) -> bool:
    """Whether a label read off the HUD is the event's label (both normalized).

    Long labels are clipped by the reading box, so the shorter of the two
    may be any part of the longer one if it has at least ``min_chars``
    characters ("SUPERHEAVYLAND" matches "SUPERHEAVYLANDING").
    """
    if not read or not label:
        return False
    if read == label:
        return True
    short, long = sorted((read, label), key=len)
    return len(short) >= min_chars and short in long


class TimelineStatusMachine:
    """The statuses of one run's timeline events."""

    def __init__(
        self,
        config: TimelineConfig = TIMELINE_CONFIG,
        now: Callable[[], str] = utc_now,
    ) -> None:
        self._config = config
        self._now = now
        self._events: tuple[TimelineEvent, ...] = ()
        self._rules = RuleEngine(config)
        self._met_filter = MetFilter(config)
        self._current: dict[str, EventStatus] = {}
        self._overrides: dict[str, _Decision] = {}
        self._clear_run()

    def _clear_run(self) -> None:
        self._rules.reset()
        self._met_filter.reset()
        self._met: float | None = None
        self._clock_source: str | None = None
        # (normalized, as read, met) of each HUD milestone label sighting
        self._sightings: list[tuple[str, str, float | None]] = []
        self._overrides = {}

    # --- Reading ---

    @property
    def events(self) -> tuple[TimelineEvent, ...]:
        return self._events

    @property
    def met(self) -> float | None:
        """The furthest MET seen this run, in seconds."""
        return self._met

    @property
    def clock_source(self) -> str | None:
        return self._clock_source

    def snapshot(self) -> list[EventStatus]:
        """Every event's status, in timeline order."""
        return [self._current[e.id] for e in self._events]

    def status(self, event_id: str) -> EventStatus:
        return self._current[event_id]

    # --- Inputs ---

    def set_timeline(self, events: Iterable[TimelineEvent | Mapping[str, Any]]) -> ChangeSet:
        """Use this timeline (time order). Statuses of ids that stay are kept."""
        timeline = tuple(
            e if isinstance(e, TimelineEvent) else TimelineEvent.model_validate(e) for e in events
        )
        ids = [e.id for e in timeline]
        if len(set(ids)) != len(ids):
            raise ValueError("timeline event ids must be unique")
        self._events = tuple(sorted(timeline, key=lambda e: e.t))
        kept = set(ids)
        self._overrides = {k: v for k, v in self._overrides.items() if k in kept}
        planned: dict[str, float] = {}
        for event in self._events:
            if event.auto.kind == "rule":
                planned.setdefault(event.auto.rule, float(event.t))
        self._rules.set_planned(planned)
        return self._recompute()

    def reset(self) -> ChangeSet:
        """A new run: forget every MET, label, verdict and override; keep the timeline."""
        self._clear_run()
        return self._recompute()

    def on_met(self, met: float, source: str = "telemetry") -> ChangeSet:
        """The mission clock reads ``met`` seconds (negative before T-0).

        Statuses follow the furthest MET seen, so a late or misread lower
        value never takes a status back. Callers feed checked readings
        (``on_record`` filters the HUD clock's misreads itself).
        """
        if not math.isfinite(met):
            return ChangeSet()
        if source not in CLOCK_SOURCES:
            raise ValueError(f"unknown clock source {source!r}")
        self._clock_source = source
        if self._met is None or met > self._met:
            self._met = float(met)
        return self._recompute()

    def on_record(self, record: Mapping[str, Any]) -> ChangeSet:
        """One telemetry record (the broadcast ``telemetry`` payload), in video order."""
        whole = parse_met(record.get("mission_elapsed_time"))
        if whole is None:
            return ChangeSet()
        timestamp = record.get("timestamp")
        met = self._met_filter.push(whole, timestamp if isinstance(timestamp, (int, float)) else None)
        if met is None:
            return ChangeSet()
        self._rules.push(read_sample(record, met))
        return self.on_met(met, "telemetry")

    def on_hud_label(self, text: str, met: float | None = None) -> ChangeSet:
        """A milestone label read off the HUD's arc at ``met`` (default: the current MET)."""
        normalized = normalize_label(text)
        if not normalized:
            return ChangeSet()
        if met is None:
            met = self._met
        for seen, _, at in self._sightings:
            if seen == normalized and (
                at is None or met is None or abs(met - at) <= self._config.hud_repeat_s
            ):
                return ChangeSet()  # the same label still scrolling past
        self._sightings.append((normalized, " ".join(text.upper().split()), met))
        return self._recompute()

    def override(self, event_id: str, status: str | None, by: str = "human") -> ChangeSet:
        """A person sets an event's status, or with None hands it back to auto.

        Raises:
            KeyError: No event has this id.
            ValueError: Not one of ``STATUSES``.
        """
        if event_id not in self._current:
            raise KeyError(event_id)
        if status is None:
            self._overrides.pop(event_id, None)
        elif status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        else:
            self._overrides[event_id] = _Decision(status, "human", f"Set by {by}", self._met)  # type: ignore[arg-type]
        return self._recompute()

    # --- Deriving ---

    def _recompute(self) -> ChangeSet:
        hud = self._assign_sightings()
        changed: list[EventStatus] = []
        current: dict[str, EventStatus] = {}
        for event in self._events:
            decision = self._overrides.get(event.id)
            if decision is not None:
                auto = self._auto(event, hud)
                note = f"auto: {auto.status.replace('_', ' ')}"
                if auto.evidence:
                    note += f", {auto.evidence}"
                decision = _Decision(decision.status, "human", f"{decision.evidence} ({note})", decision.met)
            else:
                decision = self._auto(event, hud)
            before = self._current.get(event.id)
            if before is not None and (
                before.status, before.source, before.evidence, before.met
            ) == (decision.status, decision.source, decision.evidence, decision.met):
                current[event.id] = before
                continue
            status = EventStatus(
                event.id, decision.status, decision.source, decision.evidence, decision.met, self._now()
            )
            current[event.id] = status
            changed.append(status)
        removed = tuple(k for k in self._current if k not in current)
        self._current = current
        return ChangeSet(tuple(changed), removed)

    def _auto(self, event: TimelineEvent, hud: Mapping[str, tuple[str, float | None]]) -> _Decision:
        met, t = self._met, float(event.t)
        grace = t + self._config.grace_s
        auto = event.auto
        if auto.kind == "hud" and event.id in hud:
            label, at = hud[event.id]
            return _Decision("success", "auto", f"HUD milestone {label}", at)
        if auto.kind == "rule":
            verdict = self._rules.verdict(auto.rule)
            if verdict is not None:
                return _Decision(verdict.status, "auto", verdict.evidence, verdict.met)
        if met is None:
            return _Decision("not_yet", "auto", None, None)
        if auto.kind == "manual":
            if met >= t:
                return _Decision("pending", "auto", f"{format_met(t)} passed; confirm by hand", t)
        elif auto.kind == "clock":
            if met >= t:
                return _Decision("success", "auto", f"Clock reached {format_met(t)}", t)
        elif met >= grace:
            what = f"HUD milestone {auto.label.upper()}" if auto.kind == "hud" else "telemetry evidence"
            return _Decision("pending", "auto", f"No {what} by {format_met(grace)}", grace)
        return _Decision("not_yet", "auto", None, None)

    def _assign_sightings(self) -> dict[str, tuple[str, float | None]]:
        """Which HUD event each label sighting confirms: the matching one planned closest to it."""
        assigned: dict[str, tuple[str, float | None]] = {}
        hud_events = [e for e in self._events if e.auto.kind == "hud"]
        limit = self._config.hud_max_offset_s
        for normalized, label, met in self._sightings:
            candidates = [
                e for e in hud_events
                if e.id not in assigned
                and labels_match(normalized, normalize_label(e.auto.label), self._config.hud_min_match_chars)
                and (met is None or limit is None or abs(met - e.t) <= limit)
            ]
            if candidates:
                best = min(candidates, key=lambda e: 0 if met is None else abs(met - e.t))
                assigned[best.id] = (label, met)
        return assigned

