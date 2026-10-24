"""Tunables of the timeline status engine, in one place (like the dashboard's ENGINE_EVENT_CONFIG).

Times are seconds of MET, speeds km/h. Engine counts are the planned running
counts from the flight profile (Super Heavy V2 and V3, Starship); a count in
these sets is a planned step, any other drop is an engine out.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TimelineConfig:
    """Thresholds of the status machine and its telemetry rules."""

    # --- Status machine ---
    # A hud or rule event with no decisive evidence this long after its
    # planned time becomes pending (needs a human)
    grace_s: float = 30.0

    # --- Debouncing ---
    # A running count must read the same in this many consecutive visible
    # records, spanning at least confirm_min_s, before it counts as a change
    # (phantom detections and single misreads never make a transition)
    confirm_records: int = 2
    confirm_min_s: float = 0.5
    # A count of 0 must hold at least this long: a panel fading out reads as
    # every engine off for a moment
    zero_confirm_min_s: float = 1.0
    # A MET reading more than this far from the one the video clock predicts
    # is a misread, until confirm_records consecutive readings agree with it
    met_tolerance_s: float = 10.0

    # --- Super Heavy ---
    # Ascent burn: at least this many engines running
    sh_ascent_min: int = 20
    # MECO leaves 3 (V2) or 5 (V3) running for hot staging
    sh_meco_counts: tuple[int, ...] = (3, 5)
    # Counts a shutdown step may leave without being an engine out
    # (boostback and landing burn throttle-down; 33 is the V3 boostback)
    sh_planned_counts: tuple[int, ...] = (33, 13, 5, 3, 0)
    # Counts the landing burn ends from (the last 3); ending from more is a burn cut short
    sh_landing_final_max: int = 5
    # All engines off this long before the planned MECO is a failure (an abort)
    sh_meco_early_s: float = 20.0
    # A burn starting after MECO and after this MET is the landing burn, when
    # boostback was not seen and the timeline has no planned times to tell them apart
    sh_landing_min_met_s: float = 300.0

    # --- Starship ---
    # Ascent burn: at least this many engines running
    ss_ascent_min: int = 3
    # Engines per ring (3 sea-level, 3 vacuum): staggered SECO stops one
    # ring, 6 -> 3, at most ss_seco_stagger_s before the other
    ss_ring_engines: int = 3
    ss_seco_stagger_s: float = 5.0
    # Landing burn: 3 -> 2 -> 1
    ss_landing_engines: int = 3
    # A ship burn starting after this MET, or below ss_landing_max_altitude_km,
    # is the landing burn (as in the dashboard's engine-out detector)
    ss_landing_min_met_s: float = 3000.0
    ss_landing_max_altitude_km: float = 30.0
    # A relight whose engines are all off again within this time died after relight
    relight_grace_s: float = 5.0
    # Engines lit within this time of a burn's ignition count as lit with it
    ignition_window_s: float = 3.0
    # "Never lit" needs the panel visible from the planned relight time to the
    # end of its grace window with no gap between readings longer than this
    relight_visible_gap_s: float = 5.0

    # Ship speed thresholds for the transonic and subsonic events, km/h.
    # Mach 1 depends on the air temperature, so on altitude: about 1235 km/h
    # at sea level and 1060 km/h in the stratosphere. These are sea-level
    # style values; the timeline's own time stays the plan.
    ss_transonic_kmh: float = 1235.0
    ss_subsonic_kmh: float = 1100.0
    # Speed events only count once the ship has been at least this fast, and
    # only after this MET (descent from entry, never the climb after staging)
    ss_entry_min_kmh: float = 5000.0
    ss_speed_min_met_s: float = 1800.0
    # Consecutive readings below a threshold needed to call it crossed
    speed_confirm_records: int = 3

    # --- Telemetry lost ---
    # A vehicle with no engine, speed or altitude reading for this long is lost
    vehicle_lost_s: float = 60.0
    # ... and the burn it was in counts as failed only if its engines were
    # last seen running no longer than this before its data stopped
    burn_recent_s: float = 10.0

    # --- HUD milestones ---
    # Shortest clipped label that may match a longer one
    hud_min_match_chars: int = 5
    # The same label read again within this time is the same sighting
    hud_repeat_s: float = 60.0
    # A label only confirms an event planned at most this far from it (None: any)
    hud_max_offset_s: float | None = 300.0


TIMELINE_CONFIG = TimelineConfig()
