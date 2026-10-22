import type { TimeSeriesPoint, TimeSeriesStore } from '../core/time-series';
import { parseMETToSeconds } from '../core/met';
import type { PreviousFlightColumns } from '../core/types';
import { MetClock } from '../core/met-clock';
import { EngineEventDetector } from './engines/detector';
import type { EngineEvent, EngineStatusMap } from './engines/events';

/** Rows before this MET are ignored by estimateSeparationMet (pad and clamp noise). */
export const SEPARATION_MIN_MET_S = 30;

/**
 * MET in seconds of stage separation. Separation is not recorded, but before
 * it both vehicles share one reading, so separation is where the shared run
 * ends: the row after the last one (past SEPARATION_MIN_MET_S) where both
 * speeds exist and are equal. The first differing row is no good: after
 * separation the Super Heavy speed is often unread for up to a minute, and
 * pad readings can differ. Null when there is no shared reading.
 */
export function estimateSeparationMet(
  columns: PreviousFlightColumns,
  metSeconds: number[] = estimateMetSeconds(columns),
): number | null {
  let last = -1;
  for (let i = 0; i < columns.met.length; i++) {
    if (metSeconds[i] <= SEPARATION_MIN_MET_S) continue;
    const ss = columns.ss_speed[i];
    const sh = columns.sh_speed[i];
    if (ss !== null && ss !== undefined && sh !== null && sh !== undefined && ss === sh) last = i;
  }
  if (last < 0) return null;
  for (let i = last + 1; i < metSeconds.length; i++) {
    if (metSeconds[i] > metSeconds[last]) return metSeconds[i];
  }
  // Shared to the end of the data: one row spacing after the last shared row
  const spacing = last > 0 ? metSeconds[last] - metSeconds[last - 1] : 0;
  return metSeconds[last] + spacing;
}

/**
 * Sub-second MET of every row.
 *
 * real_time_seconds is frame-based and drifts several seconds from the
 * recorded MET over a flight, so it only supplies the sub-second offset:
 * MetClock anchors it to the MET clock.
 */
export function estimateMetSeconds(columns: PreviousFlightColumns): number[] {
  const metClock = new MetClock();
  return columns.met.map((met, i) => metClock.estimate(parseMETToSeconds(met), columns.t[i] * 1000));
}

/**
 * Engine events of a previous flight, replayed from its engine status
 * changes. Separation is not recorded, so the ship altitude column (the
 * stack's before separation) stands in for the landing-burn check.
 */
export function detectFlightEngineEvents(
  columns: PreviousFlightColumns,
  metSeconds: number[] = estimateMetSeconds(columns),
): EngineEvent[] {
  const changes = columns.engines ?? [];
  if (changes.length === 0) return [];
  const detector = new EngineEventDetector();
  let starship: EngineStatusMap = {};
  let superheavy: EngineStatusMap = {};
  let next = 0;
  for (let i = 0; i < columns.met.length; i++) {
    while (next < changes.length && changes[next].i <= i) {
      starship = changes[next].ss ?? starship;
      superheavy = changes[next].sh ?? superheavy;
      next++;
    }
    detector.pushStatuses(metSeconds[i], starship, superheavy, undefined, columns.ss_alt[i] ?? undefined);
  }
  detector.flush();
  return detector.events();
}

/**
 * Convert a columnar previous flight into the TimeSeriesStore format
 * used by the charts. metSeconds is the sub-second MET of each row
 * (estimated from the columns when not given).
 */
export function transformToTimeSeries(
  columns: PreviousFlightColumns,
  metSeconds: number[] = estimateMetSeconds(columns),
): TimeSeriesStore {
  const store: TimeSeriesStore = {
    speedSuperHeavy: [],
    speedStarship: [],
    altitudeSuperHeavy: [],
    altitudeStarship: [],
  };
  const series: [(number | null)[], TimeSeriesPoint[], string][] = [
    [columns.sh_speed, store.speedSuperHeavy, 'km/h'],
    [columns.ss_speed, store.speedStarship, 'km/h'],
    [columns.sh_alt, store.altitudeSuperHeavy, 'km'],
    [columns.ss_alt, store.altitudeStarship, 'km'],
  ];

  for (let i = 0; i < columns.met.length; i++) {
    const met = columns.met[i];
    const timestamp = columns.t[i];

    for (const [values, points, unit] of series) {
      const value = values[i];
      if (value !== null && value !== undefined) {
        points.push({ missionElapsedTime: met, metSeconds: metSeconds[i], timestamp, value, unit });
      }
    }
  }

  return store;
}
