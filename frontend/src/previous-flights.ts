import type { TimeSeriesPoint, TimeSeriesStore } from './state';

/**
 * Raw record structure from previous flight JSON files.
 */
interface PreviousFlightRecord {
  frame_number: number;
  vehicles: {
    starship: {
      speed: number | null;
      altitude: number | null;
      fuel: { lox: { fullness: number }; ch4: { fullness: number } };
      engines: Record<string, unknown>;
    };
    superheavy: {
      speed: number | null;
      altitude: number | null;
      fuel: { lox: { fullness: number }; ch4: { fullness: number } };
      engines: Record<string, unknown>;
    };
  };
  time: { sign: string; hours: number; minutes: number; seconds: number };
  real_time_seconds: number;
  real_time: { hours: number; minutes: number; seconds: number; milliseconds: number };
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
  const records: PreviousFlightRecord[] = await response.json();
  return transformToTimeSeries(records);
}

/**
 * Format seconds into HH:MM:SS mission elapsed time string.
 */
function formatMET(sign: string, hours: number, minutes: number, seconds: number): string {
  const prefix = sign === '+' || sign === 'T' ? '+' : '-';
  const h = String(hours).padStart(2, '0');
  const m = String(minutes).padStart(2, '0');
  const s = String(seconds).padStart(2, '0');
  return `${prefix}${h}:${m}:${s}`;
}

/**
 * Convert raw previous flight records into the TimeSeriesStore format
 * used by the charts.
 */
function transformToTimeSeries(records: PreviousFlightRecord[]): TimeSeriesStore {
  const store: TimeSeriesStore = {
    speedSuperHeavy: [],
    speedStarship: [],
    altitudeSuperHeavy: [],
    altitudeStarship: [],
  };

  for (const record of records) {
    const met = formatMET(
      record.time.sign,
      record.time.hours,
      record.time.minutes,
      record.time.seconds,
    );
    const timestamp = record.real_time_seconds;

    // Super Heavy speed
    if (record.vehicles.superheavy.speed !== null) {
      store.speedSuperHeavy.push({
        missionElapsedTime: met,
        timestamp,
        value: record.vehicles.superheavy.speed,
        unit: 'km/h',
      });
    }

    // Starship speed
    if (record.vehicles.starship.speed !== null) {
      store.speedStarship.push({
        missionElapsedTime: met,
        timestamp,
        value: record.vehicles.starship.speed,
        unit: 'km/h',
      });
    }

    // Super Heavy altitude
    if (record.vehicles.superheavy.altitude !== null) {
      store.altitudeSuperHeavy.push({
        missionElapsedTime: met,
        timestamp,
        value: record.vehicles.superheavy.altitude,
        unit: 'km',
      });
    }

    // Starship altitude
    if (record.vehicles.starship.altitude !== null) {
      store.altitudeStarship.push({
        missionElapsedTime: met,
        timestamp,
        value: record.vehicles.starship.altitude,
        unit: 'km',
      });
    }
  }

  return store;
}
