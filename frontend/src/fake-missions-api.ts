import { missionId, type MissionChoice, type MissionSettings, type MissionsApi, type SavedMission } from './missions-api';

/**
 * In-memory stand-in for the backend's mission files, for tests. Records
 * every save, can hold saves back, and can be told to fail.
 */
export class FakeMissionsApi implements MissionsApi {
  readonly files = new Map<string, SavedMission>();
  readonly saves: MissionSettings[] = [];
  /** Recorded flights (results_previous/) offered alongside saved missions. */
  flights: { name: string; filename: string }[] = [];
  /** Make the next calls fail like an unreachable backend. */
  offline = false;
  /** While set, saves wait until release() is called. */
  private held: (() => void)[] | null = null;

  async list(): Promise<MissionChoice[]> {
    if (this.offline) throw new TypeError('Failed to fetch');
    const saved = [...this.files.values()].map((m) => ({
      id: m.id,
      mission: m.mission,
      ship: m.ship,
      booster: m.booster,
      saved: true,
    }));
    const known = new Set(saved.map((s) => s.id));
    const flights = this.flights
      .filter((f) => !known.has(f.filename))
      .map((f) => ({ id: f.filename, mission: f.name, ship: '', booster: '', saved: false }));
    return [...saved, ...flights];
  }

  async load(id: string): Promise<SavedMission | null> {
    if (this.offline) throw new TypeError('Failed to fetch');
    const file = this.files.get(id);
    return file ? structuredClone(file) : null;
  }

  async save(settings: MissionSettings): Promise<SavedMission> {
    if (this.held) await new Promise<void>((resolve) => this.held!.push(resolve));
    if (this.offline) throw new TypeError('Failed to fetch');
    const id = missionId(settings.mission);
    const record = { id, ...structuredClone(settings), updated_at: new Date().toISOString() };
    this.files.set(id, record);
    this.saves.push(structuredClone(settings));
    return record;
  }

  hold(): void {
    this.held = [];
  }

  release(): void {
    const waiting = this.held ?? [];
    this.held = null;
    for (const resolve of waiting) resolve();
  }
}
