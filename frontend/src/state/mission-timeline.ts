import type { TimelineEvent } from '../domain/timeline/types';
import type { CurrentMission } from './current-mission';
import type { TimelineStatusStore } from './timeline-status';
import { missionsApi } from '../api/missions';
import { createLogger } from '../core/logger';

const logger = createLogger('mission-timeline');

/** Reads a mission's planned events; null when it has no file. */
export type EventsLoader = (missionId: string) => Promise<readonly TimelineEvent[] | null>;

const loadFromMissionFile: EventsLoader = async (id) => (await missionsApi.load(id))?.timeline ?? null;

/**
 * The timeline the dashboard shows: the planned events (what the mission file
 * stores) together with their live statuses (what the backend decides). The
 * dashboard feeds the events from its `CurrentMission`; an overlay has no
 * mission of its own and reads them from the mission file the status snapshot
 * names. Subscribers hear about either half changing.
 */
export class MissionTimeline {
  private list: readonly TimelineEvent[];
  private listeners = new Set<() => void>();
  /** Overlays: the mission and event ids the events were last looked up for. */
  private requestedKey: string | null = null;

  constructor(readonly statuses: TimelineStatusStore, events: readonly TimelineEvent[] = []) {
    this.list = events;
    statuses.subscribe(() => this.notify());
  }

  /** The planned events in time order. */
  get events(): readonly TimelineEvent[] {
    return this.list;
  }

  setEvents(events: readonly TimelineEvent[]): void {
    this.list = events;
    this.notify();
  }

  /** Called when the events or the statuses changed; returns an unsubscribe function. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Takes the events from the dashboard's mission, now and whenever they change. */
  followMission(mission: CurrentMission): () => void {
    this.setEvents(mission.timeline);
    return mission.subscribeTimeline(() => this.setEvents(mission.timeline));
  }

  /**
   * Overlays: reads the events of the mission the status snapshots name from
   * its file (GET only), again whenever the mission or the set of events the
   * backend follows changes (the timeline was edited or another mission chosen).
   */
  followStatuses(load: EventsLoader = loadFromMissionFile): () => void {
    const check = () => {
      const snapshot = this.statuses.snapshot;
      if (!snapshot) return;
      const ids = snapshot.events.map((e) => e.event_id);
      const key = `${snapshot.mission_id ?? ''}|${ids.join('\n')}`;
      if (key === this.requestedKey) return;
      this.requestedKey = key;
      if (snapshot.mission_id === null) {
        this.setEvents([]);
        return;
      }
      if (ids.length === this.list.length && ids.every((id, i) => id === this.list[i].id)) return;
      load(snapshot.mission_id).then(
        (events) => {
          if (this.requestedKey === key) this.setEvents(events ?? []);
        },
        (error: unknown) => {
          logger.warn('Timeline events not loaded', { error: error instanceof Error ? error.message : String(error) });
          // Retry with the next snapshot
          if (this.requestedKey === key) this.requestedKey = null;
        },
      );
    };
    check();
    return this.statuses.subscribe(check);
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}
