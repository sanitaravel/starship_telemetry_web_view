import { createLogger } from '../core/logger';
import type { ShipPosition, ShipTrack } from '../core/ship-track';
import { SHIP_TRACKER_URL, fetchTrackerFeed, shipNumber } from '../api/ship-tracker-feed';

const log = createLogger('ShipTracker');

export const DEFAULT_POLL_INTERVAL_MS = 30_000;

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
      const now = Date.now();
      const samples = await fetchTrackerFeed(this.url, now, this.fetchFn);
      if (samples) this.ingest(samples, now);
    } catch (err) {
      log.warn('Tracker feed request failed', { error: String(err) });
    }
  }

  /**
   * Adds parsed samples to the tracks, stamped with the poll time if given;
   * notifies listeners if anything changed.
   */
  ingest(samples: Map<string, ShipPosition>, polledAt?: number): void {
    let changed = false;
    for (const [id, position] of samples) {
      let track = this.tracks.get(id);
      if (!track) {
        track = { id, number: shipNumber(id), positions: [] };
        this.tracks.set(id, track);
      }
      const last = track.positions[track.positions.length - 1];
      if (last && position.gpsTime <= last.gpsTime) continue;
      track.positions.push(polledAt === undefined ? position : { ...position, polledAt });
      changed = true;
    }
    if (changed) {
      const tracks = this.getTracks();
      for (const listener of this.listeners) listener(tracks);
    }
  }
}
