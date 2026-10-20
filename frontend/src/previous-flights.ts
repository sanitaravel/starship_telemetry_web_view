import type { TimeSeriesPoint, TimeSeriesStore } from './state';
import { MetClock } from './met-clock';
import { parseMETToSeconds } from './time-series-graphs/series-math';
import { EngineEventDetector, type EngineEvent, type EngineStatusMap } from './engine-events';

/**
 * A change of engine statuses at row `i` of the columns: the full map of
 * each vehicle (ss Starship, sh Super Heavy) whose statuses differ from the
 * previous entry for that vehicle. Rows in between keep the last map.
 */
export interface EngineStatusChange {
  i: number;
  ss?: EngineStatusMap;
  sh?: EngineStatusMap;
}

/**
 * Columnar previous-flight payload from `/api/previous-flights/{name}`.
 * Every array has one entry per recorded frame; ss_* is Starship, sh_* Super
 * Heavy, met is the recorded MET string and t the frame clock in seconds.
 */
export interface PreviousFlightColumns {
  met: string[];
  t: number[];
  ss_speed: (number | null)[];
  sh_speed: (number | null)[];
  ss_alt: (number | null)[];
  sh_alt: (number | null)[];
  /** Engine status changes; absent from older backends, empty when the file has no statuses. */
  engines?: EngineStatusChange[];
}

/** A loaded previous flight: chart series and the engine events detected in it. */
export interface PreviousFlightData {
  store: TimeSeriesStore;
  engineEvents: EngineEvent[];
}

/**
 * Metadata about a previous flight available from the API.
 */
export interface PreviousFlightInfo {
  name: string;
  filename: string;
}

/**
 * Fetch the list of available previous flights from the backend.
 */
export async function fetchPreviousFlightList(): Promise<PreviousFlightInfo[]> {
  const response = await fetch('/api/previous-flights');
  if (!response.ok) {
    return [];
  }
  return response.json();
}

/**
 * Fetch a specific previous flight: its TimeSeriesStore and engine events.
 */
export async function fetchPreviousFlightData(filename: string): Promise<PreviousFlightData | null> {
  const response = await fetch(`/api/previous-flights/${encodeURIComponent(filename)}`);
  if (!response.ok) {
    return null;
  }
  const columns: PreviousFlightColumns = await response.json();
  const metSeconds = estimateMetSeconds(columns);
  return {
    store: transformToTimeSeries(columns, metSeconds),
    engineEvents: detectFlightEngineEvents(columns, metSeconds),
  };
}

/**
 * Sub-second MET of every row.
 *
 * real_time_seconds is frame-based and drifts several seconds from the
 * recorded MET over a flight, so it only supplies the sub-second offset:
 * MetClock anchors it to the MET clock.
 */
function estimateMetSeconds(columns: PreviousFlightColumns): number[] {
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
