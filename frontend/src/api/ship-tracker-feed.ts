import { createLogger } from '../core/logger';
import type { ShipPosition } from '../core/ship-track';

const log = createLogger('ShipTracker');

/** Public SpaceX tracker feed. Served with `Access-Control-Allow-Origin: *`. */
export const SHIP_TRACKER_URL = 'https://content.spacex.com/cms-assets/starship_tracker_public.json';

/**
 * Appends the current time in ms as a query string. The CDN in front of the
 * feed caches responses per URL (a plain request can return a copy weeks
 * old), so each poll needs a unique URL to get the live data.
 */
export function withCacheBuster(url: string, now: number = Date.now()): string {
  return `${url}${url.includes('?') ? '&' : '?'}${now}`;
}

const SHIP_KEY_PATTERN = /^ship(\d+)$/;

/** The number in a `ship<N>` feed key, or 0 if the key has another form. */
export function shipNumber(id: string): number {
  const match = SHIP_KEY_PATTERN.exec(id);
  return match ? Number(match[1]) : 0;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function parseCurrent(raw: unknown): ShipPosition | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const c = raw as Record<string, unknown>;
  const { gps_time, mission_time, altitude, speed, latitude, longitude } = c;
  if (
    !isFiniteNumber(gps_time) ||
    !isFiniteNumber(altitude) ||
    !isFiniteNumber(latitude) ||
    !isFiniteNumber(longitude)
  ) {
    return null;
  }
  return {
    gpsTime: gps_time,
    missionTime: isFiniteNumber(mission_time) ? mission_time : 0,
    altitude,
    speed: isFiniteNumber(speed) ? speed : 0,
    latitude,
    longitude,
  };
}

/**
 * Extracts the `current` sample of every `ship<N>` entry in the feed.
 * Other top-level keys (`metadata`) and trajectories are ignored.
 * Samples with altitude below 0 are dropped.
 */
export function parseTrackerPayload(payload: unknown): Map<string, ShipPosition> {
  const result = new Map<string, ShipPosition>();
  if (typeof payload !== 'object' || payload === null) return result;

  for (const [key, value] of Object.entries(payload as Record<string, unknown>)) {
    if (!SHIP_KEY_PATTERN.test(key)) continue;
    if (typeof value !== 'object' || value === null) continue;
    const position = parseCurrent((value as Record<string, unknown>).current);
    if (position && position.altitude >= 0) {
      result.set(key, position);
    }
  }
  return result;
}

/**
 * Fetches the feed once under a cache-busting URL stamped with `now` and
 * parses it. Returns null (and logs) on an HTTP error; network and JSON
 * errors are thrown to the caller.
 */
export async function fetchTrackerFeed(
  url: string,
  now: number,
  fetchFn: typeof fetch = (...args) => fetch(...args),
): Promise<Map<string, ShipPosition> | null> {
  const response = await fetchFn(withCacheBuster(url, now), { cache: 'no-store' });
  if (!response.ok) {
    log.warn(`Tracker feed returned HTTP ${response.status}`);
    return null;
  }
  return parseTrackerPayload(await response.json());
}

