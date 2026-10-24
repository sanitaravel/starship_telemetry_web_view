import { fetchPreviousFlightList } from './previous-flights';
import type { TimelineEvent } from '../domain/timeline/types';

/** What a mission file holds (see backend/src/storage/missions.py). */
export interface MissionSettings {
  mission: string;
  ship: string;
  booster: string;
  /** Engine id -> serial, e.g. { ss_e6: 'R576' }. */
  starship_engines: Record<string, string>;
  /** Engine id -> serial, e.g. { sh_e1: 'R301' }; absent in files saved before Super Heavy had settings. */
  superheavy_engines?: Record<string, string>;
  /** Planned events in time order; absent in files saved before timelines existed. */
  timeline?: TimelineEvent[];
}

export interface SavedMission extends MissionSettings {
  id: string;
  updated_at: string | null;
}

/** A mission name the settings panel can offer. */
export interface MissionChoice {
  id: string;
  mission: string;
  /** Ship and booster of a saved mission; empty for a recorded flight without settings. */
  ship: string;
  booster: string;
  /** True when the mission has a settings file; false for a recorded flight only. */
  saved: boolean;
}

/**
 * Id of a mission: "IFT 14" -> "ift-14". Mirrors mission_id in
 * backend/src/storage/missions.py, which is also how results_previous/ names flights.
 */
export function missionId(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
    .replace(/-+$/, '');
}

/**
 * Where the backend keeps a mission's settings: "ift-14" ->
 * "missions/engines_ift-14.json". The prefix keeps it apart from the recorded
 * flight of the same name (results_previous/ift-14.json), which is never written.
 */
export function missionFile(id: string): string {
  return `missions/engines_${id}.json`;
}

/** The backend calls the panel uses; swapped out in tests. */
export interface MissionsApi {
  list(): Promise<MissionChoice[]>;
  load(id: string): Promise<SavedMission | null>;
  save(settings: MissionSettings): Promise<SavedMission>;
}

export const missionsApi: MissionsApi = {
  /**
   * Saved missions, newest first, then recorded flights (results_previous/)
   * that have no settings file yet, so a mission can be named after one.
   */
  async list() {
    const [saved, flights] = await Promise.all([
      fetch('/api/missions')
        .then((r) => (r.ok ? (r.json() as Promise<SavedMission[]>) : []))
        .catch(() => [] as SavedMission[]),
      fetchPreviousFlightList().catch(() => []),
    ]);
    const choices: MissionChoice[] = saved.map((m) => ({
      id: m.id,
      mission: m.mission,
      ship: m.ship,
      booster: m.booster,
      saved: true,
    }));
    const known = new Set(choices.map((c) => c.id));
    for (const flight of flights) {
      if (!known.has(flight.filename)) {
        choices.push({ id: flight.filename, mission: flight.name, ship: '', booster: '', saved: false });
      }
    }
    return choices;
  },

  async load(id) {
    const response = await fetch(`/api/missions/${encodeURIComponent(id)}`);
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`Loading mission failed (${response.status})`);
    return response.json();
  },

  async save(settings) {
    const response = await fetch(`/api/missions/${encodeURIComponent(missionId(settings.mission))}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    if (!response.ok) throw new Error(`Saving mission failed (${response.status})`);
    return response.json();
  },
};
