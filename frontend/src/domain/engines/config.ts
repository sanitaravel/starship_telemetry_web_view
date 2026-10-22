/**
 * Engine event detection thresholds and their defaults.
 */

import type { Vehicle } from './layout';

/** Detection thresholds. Times are seconds of MET. */
export interface EngineEventConfig {
  /** Consecutive visible readings needed to confirm a state change. */
  CONFIRM_FRAMES: number;
  /** Minimum time those readings must span. */
  CONFIRM_MIN_S: number;
  /** Shutdowns closer than this to the previous one join the same step (minimum window). */
  STEP_WINDOW_S: number;
  /**
   * The step window is at least this many times the recent spacing of
   * visible records, so sparse sampling does not split a staggered MECO.
   */
  STEP_WINDOW_SAMPLES: number;
  /** Visible-record spacings kept per vehicle for the running median. */
  SPACING_HISTORY: number;
  /**
   * Frames with no engine active right before or after a panel gap are the
   * panel fading out or in, not data, while their run is at most this long.
   */
  FADE_MAX_S: number;
  /** An engine_out this soon after a (non-first) burn starts is died_after_relight. */
  RELIGHT_GRACE_S: number;
  /** Engines that have not lit this long after a burn starts failed to light. */
  IGNITION_WINDOW_S: number;
  /**
   * A first burn starting no later than this is the liftoff burn, which is
   * never checked for failed_to_light (startup is staggered on the pad).
   */
  LIFTOFF_MAX_MET_S: number;
  /** A ship burn starting after this MET is the landing burn. */
  SHIP_LANDING_MIN_MET_S: number;
  /** A ship burn starting below this altitude (km) is the landing burn. */
  SHIP_LANDING_MAX_ALTITUDE_KM: number;
  /** Running counts a shutdown step may leave without being an event. */
  PLANNED_COUNTS: Record<Vehicle, readonly number[]>;
  /** Extra planned counts during the ship's landing burn. */
  SHIP_LANDING_PLANNED_COUNTS: readonly number[];
}

export const ENGINE_EVENT_CONFIG: Readonly<EngineEventConfig> = {
  CONFIRM_FRAMES: 2,
  CONFIRM_MIN_S: 0.5,
  STEP_WINDOW_S: 3,
  STEP_WINDOW_SAMPLES: 2.5,
  SPACING_HISTORY: 9,
  FADE_MAX_S: 1,
  RELIGHT_GRACE_S: 5,
  IGNITION_WINDOW_S: 3,
  LIFTOFF_MAX_MET_S: 30,
  SHIP_LANDING_MIN_MET_S: 3000,
  SHIP_LANDING_MAX_ALTITUDE_KM: 30,
  PLANNED_COUNTS: { superheavy: [13, 5, 3, 0], starship: [0] },
  SHIP_LANDING_PLANNED_COUNTS: [2, 1],
};
