"""The telemetry rules of ``TIMELINE_RULES``: verdicts from engine counts and speed.

Pure logic, no I/O. Feed ``RecordSample``s in video order to a
``RuleEngine``; it keeps at most one ``Verdict`` per rule (success, partial
or failure, with the evidence and the MET it happened at). A rule without a
verdict has no decisive evidence yet; the status machine turns that into
pending once the event's grace window has passed.

Each vehicle's running count is debounced first (``StableCount``): a count
must read the same in ``confirm_records`` consecutive visible records before
it is a transition, dated at the first of them, so phantom detections and
single misreads never count. A record without the vehicle's engine panel on
screen carries no information: engine icons are shown only in some flight
phases, so a hidden panel is never evidence.

Burns are followed as phases, each vehicle on its own:

- Super Heavy: ascent (>= ``sh_ascent_min``) -> MECO (down to 3 or 5) ->
  boostback (up again) -> coast (0) -> landing burn (up) -> done (0). A drop to a count
  outside ``sh_planned_counts`` during a burn is an engine out, which makes the
  burn's end partial.
- Starship: ascent (>= ``ss_ascent_min``) -> SECO (0) -> relight (0 -> 1) ->
  landing burn (0 -> 3 -> 2 -> 1 -> 0).

Failure needs explicit negative evidence: all engines off long before MECO,
a relight that dies within ``relight_grace_s``, no relight while the panel
was on screen the whole window, a landing burn ending with more engines than
planned, or the vehicle's data vanishing for ``vehicle_lost_s`` while its
engines were running (only the burn in progress fails; that verdict is taken
back if the data returns).
"""

from dataclasses import dataclass
from typing import Literal, Mapping

from src.timeline.config import TIMELINE_CONFIG, TimelineConfig
from src.timeline.samples import RecordSample, format_met

VerdictStatus = Literal["success", "partial", "failure"]

# Per burn phase, the rules that fail (the first undecided one) when the
# vehicle's telemetry is lost during it
_SH_BURN_TARGETS = {
    "ascent": ("sh_meco",),
    "boostback": ("sh_boostback_end",),
    "landing": ("sh_landing_end",),
}
_SS_BURN_TARGETS = {
    "ascent": ("ss_seco",),
    "relight": ("ss_relight",),
    "landing": ("ss_landing_start", "ss_landing_3_to_2", "ss_landing_2_to_1"),
}


@dataclass(frozen=True)
class Verdict:
    """A rule's decision: what happened, why the rule thinks so, and when."""

    status: VerdictStatus
    evidence: str
    met: float


class StableCount:
    """A vehicle's running engine count, changed only by readings that hold."""

    def __init__(self, config: TimelineConfig) -> None:
        self._config = config
        self.value: int | None = None
        self._candidate: int | None = None
        self._first = 0.0
        self._seen = 0

    def push(self, met: float, count: int | None) -> tuple[int | None, int, float] | None:
        """One record's count (None: panel not on screen).

        Returns ``(old, new, met of the first reading of new)`` when the
        stable count changes, otherwise None.
        """
        if count is None:
            return None
        if count == self.value:
            self._candidate = None
            return None
        if count != self._candidate:
            self._candidate, self._first, self._seen = count, met, 0
        self._seen += 1
        span = self._config.zero_confirm_min_s if count == 0 else self._config.confirm_min_s
        if self._seen >= self._config.confirm_records and met - self._first >= span:
            old, self.value, self._candidate = self.value, count, None
            return old, count, self._first
        return None


@dataclass
class _Burn:
    kind: str
    start: float
    peak: int
    # (met, count before, count after) of the first unplanned drop
    engine_out: tuple[float, int, int] | None = None


def _engine_out_text(vehicle: str, out: tuple[float, int, int] | None) -> str:
    if out is None:
        return ""
    met, before, after = out
    return f", after an engine out at {format_met(met)} ({before} -> {after} {vehicle} engines)"


def _engines(n: int) -> str:
    return f"{n} engine" if n == 1 else f"{n} engines"


@dataclass
class _Vehicle:
    """What both vehicles' rules track besides their own phases."""

    count: StableCount
    last_data: float | None = None
    # Last visible reading with an engine running
    last_running: float | None = None
    # First visible reading of the current unbroken run of visible readings
    visible_since: float | None = None
    last_visible: float | None = None
    lost: bool = False


class RuleEngine:
    """Verdicts of every telemetry rule, from records in video order."""

    def __init__(self, config: TimelineConfig = TIMELINE_CONFIG) -> None:
        self._config = config
        self._planned: dict[str, float] = {}
        self.reset()

    def reset(self) -> None:
        """Forget the run: no verdicts, every vehicle before its first burn."""
        self._verdicts: dict[str, Verdict] = {}
        # Failures from lost telemetry; withdrawn when the vehicle's data returns
        self._lost: dict[str, Verdict] = {}
        self._sh = _Vehicle(StableCount(self._config))
        self._ss = _Vehicle(StableCount(self._config))
        self._sh_phase = "pre"
        self._ss_phase = "pre"
        self._sh_burn: _Burn | None = None
        self._ss_burn: _Burn | None = None
        self._ss_stagger: float | None = None
        self._ss_max_speed = 0.0
        self._below: dict[str, tuple[int, float, float]] = {}

    def set_planned(self, planned: Mapping[str, float]) -> None:
        """Planned time of each rule's event (seconds from T-0), from the timeline."""
        self._planned = dict(planned)

    @property
    def verdicts(self) -> dict[str, Verdict]:
        """Every rule with a verdict."""
        return {**self._lost, **self._verdicts}

    def verdict(self, rule: str) -> Verdict | None:
        return self._verdicts.get(rule) or self._lost.get(rule)

    def push(self, sample: RecordSample) -> None:
        """Take one record into account."""
        met = sample.met
        if met >= 0:
            self._decide(
                "liftoff", "success", f"T-0 detected: first telemetry record at {format_met(met)}", met
            )
        self._track(self._sh, sample.sh_count, sample.sh_present, met)
        self._track(self._ss, sample.ss_count, sample.ss_present, met)

        change = self._sh.count.push(met, sample.sh_count)
        if change is not None:
            self._superheavy(*change)
        change = self._ss.count.push(met, sample.ss_count)
        if change is not None:
            self._starship(*change, altitude=sample.ss_altitude_km)
        self._starship_timers(met, sample.ss_count is not None)
        self._speed(met, sample.ss_speed_kmh)
        self._check_lost(met)

    # --- Shared ---

    def _decide(self, rule: str, status: VerdictStatus, evidence: str, met: float) -> None:
        """Record a verdict; the first one for a rule stands."""
        if rule not in self._verdicts:
            self._verdicts[rule] = Verdict(status, evidence, met)
            self._lost.pop(rule, None)

    def _track(self, vehicle: _Vehicle, count: int | None, present: bool, met: float) -> None:
        if present:
            vehicle.last_data = met
            if vehicle.lost:
                vehicle.lost = False
                self._withdraw_lost(vehicle)
        if count is None:
            return
        if vehicle.last_visible is None or met - vehicle.last_visible > self._config.relight_visible_gap_s:
            vehicle.visible_since = met
        vehicle.last_visible = met
        if count > 0:
            vehicle.last_running = met

    def _withdraw_lost(self, vehicle: _Vehicle) -> None:
        prefix = "sh_" if vehicle is self._sh else "ss_"
        for rule in [r for r in self._lost if r.startswith(prefix)]:
            del self._lost[rule]

    def _closer_to(self, met: float, rule: str, other: str) -> bool | None:
        """Whether met is closer to rule's planned time than to other's; None if not both planned."""
        a, b = self._planned.get(rule), self._planned.get(other)
        if a is None or b is None:
            return None
        return abs(met - a) < abs(met - b)

    # --- Super Heavy ---

    def _superheavy(self, old: int | None, new: int, t: float) -> None:
        cfg = self._config
        phase = self._sh_phase
        if phase == "pre":
            if new >= cfg.sh_ascent_min:
                self._sh_phase = "ascent"
                self._sh_burn = _Burn("ascent", t, new)
            return
        burn = self._sh_burn
        if phase == "ascent":
            assert burn is not None and old is not None
            if new > old:
                burn.peak = max(burn.peak, new)
            elif new > max(cfg.sh_meco_counts):
                burn.engine_out = burn.engine_out or (t, old, new)
            else:
                self._sh_meco(burn, old, new, t)
                self._sh_phase = "after_meco"
                self._sh_burn = None
            return
        if phase in ("after_meco", "coast"):
            floor = max(cfg.sh_meco_counts) if phase == "after_meco" else 0
            if old is not None and new > old and new > floor:
                landing = phase == "coast"
                if not landing:
                    closer = self._closer_to(t, "sh_landing_start", "sh_boostback_start")
                    landing = closer if closer is not None else t > cfg.sh_landing_min_met_s
                kind = "landing" if landing else "boostback"
                rule = "sh_landing_start" if landing else "sh_boostback_start"
                name = "landing burn" if landing else "boostback"
                self._decide(
                    rule, "success",
                    f"Super Heavy {name} start at {format_met(t)} ({old} -> {_engines(new)})", t,
                )
                self._sh_phase = kind
                self._sh_burn = _Burn(kind, t, new)
            return
        if phase in ("boostback", "landing"):
            assert burn is not None and old is not None
            if new > old:
                burn.peak = max(burn.peak, new)
                return
            if new > 0:
                if new not in cfg.sh_planned_counts:
                    burn.engine_out = burn.engine_out or (t, old, new)
                return
            out = _engine_out_text("Super Heavy", burn.engine_out)
            status: VerdictStatus = "partial" if burn.engine_out else "success"
            if phase == "boostback":
                self._decide(
                    "sh_boostback_end", status,
                    f"Super Heavy boostback end at {format_met(t)} ({old} -> 0 engines){out}", t,
                )
                self._sh_phase = "coast"
            else:
                if old > cfg.sh_landing_final_max:
                    self._decide(
                        "sh_landing_end", "failure",
                        f"Super Heavy landing burn cut short at {format_met(t)}: "
                        f"{old} -> 0 engines (planned to end on 3)", t,
                    )
                else:
                    self._decide(
                        "sh_landing_end", status,
                        f"Super Heavy landing burn end at {format_met(t)} ({old} -> 0 engines){out}", t,
                    )
                self._sh_phase = "done"
            self._sh_burn = None

    def _sh_meco(self, burn: _Burn, old: int, new: int, t: float) -> None:
        cfg = self._config
        if new == 0:
            planned = self._planned.get("sh_meco")
            if planned is not None and t < planned - cfg.sh_meco_early_s:
                self._decide(
                    "sh_meco", "failure",
                    f"Super Heavy engines all off at {format_met(t)} ({old} -> 0), "
                    f"before the planned MECO at {format_met(planned)}", t,
                )
            return  # all off around MECO time: not decisive either way
        counts = " or ".join(str(c) for c in cfg.sh_meco_counts)
        out = _engine_out_text("Super Heavy", burn.engine_out)
        if new not in cfg.sh_meco_counts:
            self._decide(
                "sh_meco", "partial",
                f"Super Heavy MECO at {format_met(t)} left {_engines(new)} (planned {counts}){out}", t,
            )
        else:
            status: VerdictStatus = "partial" if burn.engine_out else "success"
            self._decide(
                "sh_meco", status,
                f"Super Heavy MECO at {format_met(t)} ({burn.peak} -> {_engines(new)}){out}", t,
            )

    # --- Starship ---

    def _starship(self, old: int | None, new: int, t: float, altitude: float | None) -> None:
        cfg = self._config
        phase = self._ss_phase
        burn = self._ss_burn
        if phase == "pre":
            if new >= cfg.ss_ascent_min:
                self._ss_phase = "ascent"
                self._ss_burn = _Burn("ascent", t, new)
            return
        assert old is not None
        if phase == "ascent":
            assert burn is not None
            if new > old:
                burn.peak = max(burn.peak, new)
                self._ss_stagger = None
            elif new == 0:
                out = _engine_out_text("Starship", burn.engine_out)
                status: VerdictStatus = "partial" if burn.engine_out else "success"
                self._decide(
                    "ss_seco", status,
                    f"Starship SECO at {format_met(t)} ({burn.peak} -> 0 engines){out}", t,
                )
                self._ss_phase, self._ss_burn, self._ss_stagger = "coast", None, None
            elif old == 2 * new and new == cfg.ss_ring_engines:
                # One ring of three stopping first: staggered SECO, unless the other keeps going
                self._ss_stagger = t
            else:
                burn.engine_out = burn.engine_out or (t, old, new)
            return
        if phase == "coast":
            if new <= old:
                return
            landing = t > cfg.ss_landing_min_met_s or (
                altitude is not None and altitude < cfg.ss_landing_max_altitude_km
            )
            if landing:
                self._ss_phase = "landing"
                self._ss_burn = _Burn("landing", t, new)
                if new >= cfg.ss_landing_engines:
                    self._landing_lit(t, new)
            elif "ss_relight" not in self._verdicts:
                self._ss_phase = "relight"
                self._ss_burn = _Burn("relight", t, new)
            return
        if phase == "relight":
            assert burn is not None
            if new == 0:
                if t - burn.start <= cfg.relight_grace_s:
                    self._decide(
                        "ss_relight", "failure",
                        f"Raptor relight at {format_met(burn.start)} died after "
                        f"{t - burn.start:.0f} s", t,
                    )
                self._ss_phase, self._ss_burn = "coast", None
            return
        if phase == "landing":
            assert burn is not None
            if new > old:
                burn.peak = max(burn.peak, new)
                if new >= cfg.ss_landing_engines and t - burn.start <= cfg.ignition_window_s:
                    self._landing_lit(burn.start, new)
                return
            if new == 0:
                if old >= cfg.ss_landing_engines:
                    for rule, step in (("ss_landing_3_to_2", "3 -> 2"), ("ss_landing_2_to_1", "2 -> 1")):
                        self._decide(
                            rule, "failure",
                            f"Starship landing burn ended at {format_met(t)} with {_engines(old)} "
                            f"running, before {step}", t,
                        )
                self._ss_phase, self._ss_burn = "done", None
                return
            if new == 2 and old >= 3:
                self._decide("ss_landing_3_to_2", "success", f"Starship landing burn {old} -> 2 engines at {format_met(t)}", t)
            elif new == 1 and old >= 3:
                text = f"Starship landing burn {old} -> 1 engine at {format_met(t)}"
                self._decide("ss_landing_3_to_2", "success", text, t)
                self._decide("ss_landing_2_to_1", "success", text, t)
            elif new == 1 and old == 2:
                self._decide("ss_landing_2_to_1", "success", f"Starship landing burn 2 -> 1 engine at {format_met(t)}", t)

    def _landing_lit(self, start: float, count: int) -> None:
        self._decide(
            "ss_landing_start", "success",
            f"Starship landing burn start at {format_met(start)} ({_engines(count)})", start,
        )

    def _starship_timers(self, met: float, visible: bool) -> None:
        """Decisions that come from time passing rather than a count change."""
        cfg = self._config
        burn = self._ss_burn
        if self._ss_phase == "ascent" and burn is not None and self._ss_stagger is not None:
            if met - self._ss_stagger > cfg.ss_seco_stagger_s and self._ss.count.value:
                burn.engine_out = burn.engine_out or (self._ss_stagger, 2 * cfg.ss_ring_engines, cfg.ss_ring_engines)
                self._ss_stagger = None
        if self._ss_phase == "relight" and burn is not None:
            if met - burn.start > cfg.relight_grace_s and self._ss.count.value:
                self._decide(
                    "ss_relight", "success",
                    f"Raptor relight at {format_met(burn.start)} ({_engines(burn.peak)}), "
                    f"still running after {cfg.relight_grace_s:.0f} s", burn.start,
                )
        if self._ss_phase == "landing" and burn is not None and "ss_landing_start" not in self._verdicts:
            if met - burn.start > cfg.ignition_window_s:
                self._decide(
                    "ss_landing_start", "partial",
                    f"Starship landing burn start at {format_met(burn.start)} with "
                    f"{_engines(burn.peak)} of {cfg.ss_landing_engines}", burn.start,
                )
        planned = self._planned.get("ss_relight")
        if (
            planned is not None
            and visible
            and self._ss_phase == "coast"
            and self._ss.count.value == 0
            and met >= planned + cfg.grace_s
            and self._ss.visible_since is not None
            and self._ss.visible_since <= planned + cfg.relight_visible_gap_s
        ):
            self._decide(
                "ss_relight", "failure",
                f"No relight: Starship engines stayed off from {format_met(planned)} "
                f"to {format_met(met)} with the engine panel on screen", met,
            )

    def _speed(self, met: float, speed: float | None) -> None:
        cfg = self._config
        if speed is None:
            return
        self._ss_max_speed = max(self._ss_max_speed, speed)
        if self._ss_max_speed < cfg.ss_entry_min_kmh or met < cfg.ss_speed_min_met_s:
            return
        for rule, threshold, name in (
            ("ss_transonic", cfg.ss_transonic_kmh, "transonic"),
            ("ss_subsonic", cfg.ss_subsonic_kmh, "subsonic"),
        ):
            if speed >= threshold:
                self._below.pop(rule, None)
                continue
            seen, first, first_speed = self._below.get(rule, (0, met, speed))
            seen += 1
            self._below[rule] = (seen, first, first_speed)
            if seen >= cfg.speed_confirm_records:
                self._decide(
                    rule, "success",
                    f"Starship {name}: speed fell below {threshold:.0f} km/h at "
                    f"{format_met(first)} ({first_speed:.0f} km/h)", first,
                )

    # --- Telemetry lost ---

    def _check_lost(self, met: float) -> None:
        cfg = self._config
        for vehicle, name, phase, burn, targets in (
            (self._sh, "Super Heavy", self._sh_phase, self._sh_burn, _SH_BURN_TARGETS),
            (self._ss, "Starship", self._ss_phase, self._ss_burn, _SS_BURN_TARGETS),
        ):
            if vehicle.lost or vehicle.last_data is None or met - vehicle.last_data <= cfg.vehicle_lost_s:
                continue
            vehicle.lost = True
            running = (
                burn is not None
                and vehicle.count.value
                and vehicle.last_running is not None
                and vehicle.last_data - vehicle.last_running <= cfg.burn_recent_s
            )
            if not running:
                continue
            # The undecided rule the burn in progress was heading for
            rule = next((r for r in targets.get(phase, ()) if r not in self._verdicts), None)
            if rule is None:
                continue
            self._lost[rule] = Verdict(
                "failure",
                f"{name} telemetry lost at {format_met(vehicle.last_data)} during the {phase} burn "
                f"({_engines(vehicle.count.value or 0)} running)",
                vehicle.last_data,
            )
