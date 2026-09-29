import { createLogger } from '../logger';

const log = createLogger('ShipHistory');

/** One recorded flight as listed by `GET /api/trajectories`. */
export interface TrajectoryInfo {
  /** e.g. `ship39` */
  name: string;
  number: number;
  /** e.g. `Ship 39` */
  label: string;
}

/** A recorded flight path ready to draw: [longitude, latitude] pairs in time order. */
export interface HistoricalTrajectory extends TrajectoryInfo {
  color: string;
  coordinates: [number, number][];
}

/** Path colours for recorded flights; kept clear of the orange used for the live ship. */
export const HISTORY_COLORS = ['#4fc3f7', '#ba68c8', '#81c784', '#ffd54f', '#f06292', '#90a4ae'];

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * Converts the points served by `GET /api/trajectories/{name}` into
 * [longitude, latitude] pairs, skipping malformed or below-ground points.
 */
export function toCoordinates(raw: unknown): [number, number][] {
  if (!Array.isArray(raw)) return [];
  const coordinates: [number, number][] = [];
  for (const point of raw) {
    if (typeof point !== 'object' || point === null) continue;
    const { latitude, longitude, altitude } = point as Record<string, unknown>;
    if (!isFiniteNumber(latitude) || !isFiniteNumber(longitude)) continue;
    if (isFiniteNumber(altitude) && altitude < 0) continue;
    coordinates.push([longitude, latitude]);
  }
  return coordinates;
}

/**
 * Loads every recorded flight from the backend. Flights that fail to load
 * or have fewer than two points are left out.
 */
export async function fetchHistoricalTrajectories(
  fetchFn: typeof fetch = (...args) => fetch(...args),
): Promise<HistoricalTrajectory[]> {
  let list: TrajectoryInfo[];
  try {
    const response = await fetchFn('/api/trajectories');
    if (!response.ok) return [];
    list = await response.json();
  } catch (err) {
    log.warn('Failed to list recorded trajectories', { error: String(err) });
    return [];
  }

  const loaded = await Promise.all(
    list.map(async (info, i): Promise<HistoricalTrajectory | null> => {
      try {
        const response = await fetchFn(`/api/trajectories/${encodeURIComponent(info.name)}`);
        if (!response.ok) return null;
        const coordinates = toCoordinates(await response.json());
        if (coordinates.length < 2) return null;
        return { ...info, color: HISTORY_COLORS[i % HISTORY_COLORS.length], coordinates };
      } catch (err) {
        log.warn('Failed to load recorded trajectory', { name: info.name, error: String(err) });
        return null;
      }
    }),
  );
  return loaded.filter((t): t is HistoricalTrajectory => t !== null);
}
