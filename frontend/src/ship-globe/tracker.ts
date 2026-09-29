import { createLogger } from '../logger';

const log = createLogger('ShipTracker');

/** Public SpaceX tracker feed. Served with `Access-Control-Allow-Origin: *`. */
export const SHIP_TRACKER_URL = 'https://content.spacex.com/cms-assets/starship_tracker_public.json';

/** The CDN caches the feed for up to 10 minutes; polling faster only re-reads the cache. */
export const DEFAULT_POLL_INTERVAL_MS = 30_000;

const SHIP_KEY_PATTERN = /^ship(\d+)$/;

/** One `current` sample from the feed. Altitude is metres, speed is m/s. */
export interface ShipPosition {
  gpsTime: number;
  missionTime: number;
  altitude: number;
  speed: number;
  latitude: number;
  longitude: number;
}

export interface ShipTrack {
  /** Feed key, e.g. `ship40`. */
  id: string;
  /** Vehicle number parsed from the key, e.g. 40. */
  number: number;
  /** Samples in gps_time order; only those with altitude >= 0 are kept. */
  positions: ShipPosition[];
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

type TrackListener = (tracks: ShipTrack[]) => void;

/**
 * Polls the tracker feed and accumulates each ship's `current` samples
 * into a track. Repeated samples (same gps_time) are stored once.
 */
export class ShipTracker {
  private tracks = new Map<string, ShipTrack>();
  private listeners: TrackListener[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly url: string = SHIP_TRACKER_URL,
    private readonly intervalMs: number = DEFAULT_POLL_INTERVAL_MS,
    private readonly fetchFn: typeof fetch = (...args) => fetch(...args),
  ) {}

  start(): void {
    if (this.timer !== null) return;
    void this.poll();
    this.timer = setInterval(() => void this.poll(), this.intervalMs);
  }

  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  subscribe(listener: TrackListener): () => void {
    this.listeners.push(listener);
    listener(this.getTracks());
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  getTracks(): ShipTrack[] {
    return [...this.tracks.values()].sort((a, b) => a.number - b.number);
  }

  async poll(): Promise<void> {
    try {
      const response = await this.fetchFn(this.url, { cache: 'no-cache' });
      if (!response.ok) {
        log.warn(`Tracker feed returned HTTP ${response.status}`);
        return;
      }
      this.ingest(parseTrackerPayload(await response.json()));
    } catch (err) {
      log.warn('Tracker feed request failed', { error: String(err) });
    }
  }

  /** Adds parsed samples to the tracks; notifies listeners if anything changed. */
  ingest(samples: Map<string, ShipPosition>): void {
    let changed = false;
    for (const [id, position] of samples) {
      let track = this.tracks.get(id);
      if (!track) {
        const match = SHIP_KEY_PATTERN.exec(id);
        track = { id, number: match ? Number(match[1]) : 0, positions: [] };
        this.tracks.set(id, track);
      }
      const last = track.positions[track.positions.length - 1];
      if (last && position.gpsTime <= last.gpsTime) continue;
      track.positions.push(position);
      changed = true;
    }
    if (changed) {
      const tracks = this.getTracks();
      for (const listener of this.listeners) listener(tracks);
    }
  }
}
