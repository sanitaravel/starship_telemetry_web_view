import type { Vehicle } from '../domain/engines/layout';
import type { TimelineEvent } from '../domain/timeline/types';
import type { EngineSerials } from './engine-serials';
import {
  missionFile,
  missionId,
  missionsApi,
  type MissionChoice,
  type MissionSettings,
  type MissionsApi,
  type SavedMission,
} from '../api/missions';

/** The fields a vehicle's name and engine serials are saved under in a mission file. */
const FIELDS = {
  starship: { name: 'ship', engines: 'starship_engines' },
  superheavy: { name: 'booster', engines: 'superheavy_engines' },
} as const;

const VEHICLES: Vehicle[] = ['starship', 'superheavy'];

/** Where the engine settings stand relative to the mission's file. */
export type SaveStatus =
  | { kind: 'unnamed' } // no mission name yet: kept in this browser only
  | { kind: 'idle'; file: string } // named, file read or untouched since the page loaded
  | { kind: 'saving' }
  | { kind: 'saved'; file: string }
  | { kind: 'error'; message: string };

const STORAGE_KEY = 'engine-mission.current';

/** Why a mission could not be read or written, for the panel's status line. */
function notSaved(error: unknown): string {
  // fetch rejects with a TypeError when the request never got an answer
  return error instanceof TypeError ? 'Not saved: backend unreachable' : 'Not saved: server error';
}

/** Serial edits are written this long after the last keystroke. */
const SAVE_DELAY_MS = 400;

/**
 * The mission whose engine serials are loaded: its name, the names of its
 * ship and booster, and both vehicles' serials. One flight has one mission,
 * so choosing a mission in either vehicle's panel switches both. The browser
 * keeps all of it across page loads; once the mission has a name, every
 * change is also written to that mission's own file through the backend
 * (missions/engines_<id>.json). The mission's timeline travels with it: it is
 * read from the file and written back with every save.
 */
export class CurrentMission {
  private missionName = '';
  private names: Record<Vehicle, string> = { starship: '', superheavy: '' };
  private statusValue: SaveStatus = { kind: 'unnamed' };
  private listeners = new Set<() => void>();
  private timelineValue: readonly TimelineEvent[] = [];
  private timelineListeners = new Set<() => void>();
  /** The remembered mission's file being read at start-up; a save waits for it so it cannot overwrite the timeline. */
  private startupRead: Promise<void> = Promise.resolve();
  private timer: ReturnType<typeof setTimeout> | null = null;
  /** Set while a loaded mission's serials are applied, so they are not saved back. */
  private applying = false;
  /** One write at a time, so an older one can never land after a newer one. */
  private writing: Promise<void> = Promise.resolve();

  constructor(
    private readonly serials: Record<Vehicle, EngineSerials>,
    private readonly api: MissionsApi = missionsApi,
    private readonly saveDelayMs = SAVE_DELAY_MS
  ) {
    this.restore();
    this.readTimelineOfRememberedMission();
    for (const vehicle of VEHICLES) {
      serials[vehicle].subscribe(() => {
        if (!this.applying) this.saveSoon();
      });
    }
  }

  get mission(): string {
    return this.missionName;
  }

  get status(): SaveStatus {
    return this.statusValue;
  }

  /** The mission's timeline, in time order; empty when it has none. */
  get timeline(): readonly TimelineEvent[] {
    return this.timelineValue;
  }

  /**
   * Replaces the mission's timeline and saves it with the mission's file.
   * The caller supplies valid events in time order (see the timeline editor);
   * the backend validates and sorts again.
   */
  setTimeline(events: readonly TimelineEvent[]): void {
    this.timelineValue = structuredClone([...events]);
    this.notifyTimeline();
    this.saveSoon(0);
  }

  /** Called whenever the timeline changes, including when a mission's file is loaded; returns an unsubscribe function. */
  subscribeTimeline(listener: () => void): () => void {
    this.timelineListeners.add(listener);
    return () => this.timelineListeners.delete(listener);
  }

  /** The vehicle's name in this mission, e.g. "S41" for the ship or "B15" for the booster. */
  vehicleName(vehicle: Vehicle): string {
    return this.names[vehicle];
  }

  /**
   * Switches to the mission named `name`. A mission that already has a file
   * is loaded from it (names and serials of both vehicles). A new one gets
   * its own file: it takes over what was typed while no mission was named,
   * but starts empty when coming from another named mission, whose vehicles
   * are not this one's.
   */
  async choose(name: string): Promise<void> {
    const trimmed = name.trim();
    const id = missionId(trimmed);
    if (id && id === missionId(this.missionName)) {
      // Same mission, maybe spelt differently: keep the new spelling
      this.missionName = trimmed;
      this.persist();
      this.saveSoon(0);
      return;
    }
    const fromNamedMission = missionId(this.missionName) !== '';
    await this.flush(); // the previous mission's last edit goes to its own file

    if (!id) {
      this.missionName = '';
      this.persist();
      this.setStatus({ kind: 'unnamed' });
      return;
    }

    let saved: SavedMission | null;
    try {
      saved = await this.api.load(id);
    } catch (error) {
      this.missionName = trimmed;
      this.persist();
      this.setStatus({ kind: 'error', message: notSaved(error) });
      return;
    }

    if (saved) {
      this.missionName = saved.mission;
      this.load(saved);
      this.setStatus({ kind: 'idle', file: missionFile(id) }); // read, not written
    } else {
      this.missionName = trimmed;
      if (fromNamedMission) this.load(null);
      else this.persist();
      this.saveSoon(0);
    }
  }

  /** Missions to offer: saved ones first, then recorded flights. */
  listChoices(): Promise<MissionChoice[]> {
    return this.api.list();
  }

  /** Sets a vehicle's name, e.g. "S41" or "B15", and saves it with the mission. */
  setVehicleName(vehicle: Vehicle, name: string): void {
    const trimmed = name.trim();
    if (trimmed === this.names[vehicle]) return;
    this.names = { ...this.names, [vehicle]: trimmed };
    this.persist();
    this.saveSoon(0);
  }

  /** Writes any pending change now. */
  async flush(): Promise<void> {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
      await this.write();
    }
    await this.writing;
  }

  /** Called after every change; returns an unsubscribe function. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Puts a mission's names, serials and timeline in place (or empties them) without saving them back. */
  private load(saved: SavedMission | null): void {
    this.setTimelineSilently(saved?.timeline ?? []);
    for (const vehicle of VEHICLES) {
      this.names[vehicle] = saved?.[FIELDS[vehicle].name] ?? '';
    }
    this.persist();
    this.applying = true;
    try {
      for (const vehicle of VEHICLES) {
        // Files saved before Super Heavy had settings have no superheavy_engines
        this.serials[vehicle].replaceAll(saved?.[FIELDS[vehicle].engines] ?? {});
      }
    } finally {
      this.applying = false;
    }
  }

  private setTimelineSilently(events: readonly TimelineEvent[]): void {
    this.timelineValue = events;
    this.notifyTimeline();
  }

  private notifyTimeline(): void {
    for (const listener of this.timelineListeners) listener();
  }

  /**
   * Only the names are remembered by the browser, so a page load reads the
   * timeline from the mission's file. Anything set in the meantime wins.
   */
  private readTimelineOfRememberedMission(): void {
    const id = missionId(this.missionName);
    if (!id) return;
    const before = this.timelineValue;
    this.startupRead = this.api
      .load(id)
      .then((saved) => {
        if (saved?.timeline && this.timelineValue === before && missionId(this.missionName) === id) {
          this.setTimelineSilently(saved.timeline);
        }
      })
      .catch(() => undefined); // unreachable backend: the status line shows it on the next save
  }

  private saveSoon(delay = this.saveDelayMs): void {
    if (!missionId(this.missionName)) {
      this.setStatus({ kind: 'unnamed' });
      return;
    }
    if (this.timer !== null) clearTimeout(this.timer);
    this.setStatus({ kind: 'saving' });
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.write();
    }, delay);
  }

  private write(): Promise<void> {
    this.writing = this.writing.then(async () => {
      await this.startupRead;
      // Read what to save only now, after the start-up read may have filled in the timeline
      const settings: MissionSettings = {
        mission: this.missionName,
        ship: this.names.starship,
        booster: this.names.superheavy,
        starship_engines: this.serials.starship.snapshot(),
        superheavy_engines: this.serials.superheavy.snapshot(),
        timeline: structuredClone([...this.timelineValue]),
      };
      this.setStatus({ kind: 'saving' });
      try {
        const saved = await this.api.save(settings);
        // A newer edit may already be waiting; only report what is final
        if (this.timer === null) this.setStatus({ kind: 'saved', file: missionFile(saved.id) });
      } catch (error) {
        this.setStatus({ kind: 'error', message: notSaved(error) });
      }
    });
    return this.writing;
  }

  private setStatus(status: SaveStatus): void {
    this.statusValue = status;
    for (const listener of this.listeners) listener();
  }

  private restore(): void {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as unknown;
      if (typeof saved === 'object' && saved !== null) {
        const stored = saved as Record<string, unknown>;
        if (typeof stored.mission === 'string') this.missionName = stored.mission;
        for (const vehicle of VEHICLES) {
          const value = stored[FIELDS[vehicle].name];
          if (typeof value === 'string') this.names[vehicle] = value;
        }
      }
    } catch {
      // Storage blocked or corrupt JSON: start unnamed
    }
    if (missionId(this.missionName)) {
      this.statusValue = { kind: 'idle', file: missionFile(missionId(this.missionName)) };
    }
  }

  private persist(): void {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ mission: this.missionName, ship: this.names.starship, booster: this.names.superheavy })
      );
    } catch {
      // Not persisting is fine; it still applies for this page view
    }
    for (const listener of this.listeners) listener();
  }
}
