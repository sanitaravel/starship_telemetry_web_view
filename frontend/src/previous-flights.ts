import type { TimeSeriesPoint, TimeSeriesStore } from './state';
import { MetClock } from './met-clock';
import { parseMETToSeconds } from './time-series-graphs/series-math';

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
 * Fetch and transform a specific previous flight into TimeSeriesStore format.
 */
export async function fetchPreviousFlightData(filename: string): Promise<TimeSeriesStore | null> {
  const response = await fetch(`/api/previous-flights/${encodeURIComponent(filename)}`);
  if (!response.ok) {
    return null;
  }
  const columns: PreviousFlightColumns = await response.json();
  return transformToTimeSeries(columns);
}

/**
 * Convert a columnar previous flight into the TimeSeriesStore format
 * used by the charts.
 *
 * real_time_seconds is frame-based and drifts several seconds from the
 * recorded MET over a flight, so it only supplies the sub-second offset:
 * MetClock anchors it to the MET clock.
 */
export function transformToTimeSeries(columns: PreviousFlightColumns): TimeSeriesStore {
  const store: TimeSeriesStore = {
    speedSuperHeavy: [],
    speedStarship: [],
    altitudeSuperHeavy: [],
    altitudeStarship: [],
  };
  const metClock = new MetClock();
  const series: [(number | null)[], TimeSeriesPoint[], string][] = [
    [columns.sh_speed, store.speedSuperHeavy, 'km/h'],
    [columns.ss_speed, store.speedStarship, 'km/h'],
    [columns.sh_alt, store.altitudeSuperHeavy, 'km'],
    [columns.ss_alt, store.altitudeStarship, 'km'],
  ];

  for (let i = 0; i < columns.met.length; i++) {
    const met = columns.met[i];
    const timestamp = columns.t[i];
    const metSeconds = metClock.estimate(parseMETToSeconds(met), timestamp * 1000);

    for (const [values, points, unit] of series) {
      const value = values[i];
      if (value !== null && value !== undefined) {
        points.push({ missionElapsedTime: met, metSeconds, timestamp, value, unit });
      }
    }
  }

  return store;
}
