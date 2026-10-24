import type { TimelineEventStatus, TimelineStatusPayload, WebSocketMessage } from '../core/types';
import { fetchTimelineStatus } from '../api/timeline';
import { createLogger } from '../core/logger';

const logger = createLogger('timeline-status');

/**
 * The mission timeline's live statuses, as the backend decides them. Fed by
 * the `timeline_status` WebSocket message (sent on every change and after
 * the replay, so a reconnect catches up) and, on page load, by
 * `GET /api/timeline/status`. Each snapshot is complete; one with a lower
 * revision than the one held arrived late and is ignored. Read-only: a
 * person's override goes through `overrideTimelineStatus` (api/timeline.ts),
 * and comes back here as the next snapshot.
 */
export class TimelineStatusStore {
  private current: TimelineStatusPayload | null = null;
  private byId = new Map<string, TimelineEventStatus>();
  private listeners = new Set<() => void>();

  /** The latest snapshot, or null before the first one. */
  get snapshot(): TimelineStatusPayload | null {
    return this.current;
  }

  /** Events waiting for a person. */
  get pending(): number {
    return this.current?.pending ?? 0;
  }

  /** One event's status, or undefined when the backend does not follow that event. */
  status(eventId: string): TimelineEventStatus | undefined {
    return this.byId.get(eventId);
  }

  /** Takes a snapshot unless it is older than the one held; returns whether it was taken. */
  apply(payload: TimelineStatusPayload): boolean {
    if (this.current !== null && payload.revision <= this.current.revision) return false;
    this.current = payload;
    this.byId = new Map(payload.events.map((e) => [e.event_id, e]));
    for (const listener of this.listeners) listener();
    return true;
  }

  /** Pass every WebSocket message; only `timeline_status` is used. */
  handleMessage(message: WebSocketMessage): void {
    if (message.type === 'timeline_status') this.apply(message.payload as TimelineStatusPayload);
  }

  /** Reads the current snapshot from the backend (GET only, so overlays may call it too). */
  async load(fetcher: () => Promise<TimelineStatusPayload | null> = fetchTimelineStatus): Promise<void> {
    try {
      const payload = await fetcher();
      if (payload) this.apply(payload);
    } catch (error) {
      logger.warn('Timeline statuses not loaded', { error: error instanceof Error ? error.message : String(error) });
    }
  }

  /** Called after every snapshot taken; returns an unsubscribe function. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}
