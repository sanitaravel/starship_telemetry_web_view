/** Longest serial accepted; real ones look like "R576". */
export const MAX_SERIAL_LENGTH = 8;

/**
 * Cleans what was typed into an engine serial: upper case, letters, digits
 * and dashes only, at most MAX_SERIAL_LENGTH characters. An empty result
 * means "no serial".
 */
export function normaliseSerial(raw: string): string {
  return raw
    .toUpperCase()
    .replace(/[^A-Z0-9-]/g, '')
    .slice(0, MAX_SERIAL_LENGTH);
}

/**
 * Engine serial numbers entered by the user for the current mission (e.g.
 * "R576" for Starship's E6), keyed by engine id and saved in localStorage.
 * Each vehicle has its own namespace.
 */
export class EngineSerials {
  private readonly storageKey: string;
  private serials: Record<string, string>;
  private listeners = new Set<() => void>();

  constructor(vehicle: string) {
    this.storageKey = `engine-serials.${vehicle}`;
    this.serials = this.load();
  }

  /** The engine's serial, or undefined when none has been entered. */
  get(engineId: string): string | undefined {
    return this.serials[engineId];
  }

  /** Stores the cleaned serial; an empty one removes the engine's entry. */
  set(engineId: string, raw: string): void {
    const serial = normaliseSerial(raw);
    if ((this.serials[engineId] ?? '') === serial) return;
    const { [engineId]: _previous, ...rest } = this.serials;
    this.serials = serial ? { ...rest, [engineId]: serial } : rest;
    this.save();
  }

  /** Every serial set, by engine id. */
  snapshot(): Record<string, string> {
    return { ...this.serials };
  }

  /** Replaces all serials at once, e.g. with a saved mission's. */
  replaceAll(serials: Record<string, string>): void {
    const next: Record<string, string> = {};
    for (const [id, raw] of Object.entries(serials)) {
      const serial = normaliseSerial(raw);
      if (serial) next[id] = serial;
    }
    if (JSON.stringify(next) === JSON.stringify(this.serials)) return;
    this.serials = next;
    this.save();
  }

  /** Forgets every serial, e.g. before a new mission. */
  clear(): void {
    if (Object.keys(this.serials).length === 0) return;
    this.serials = {};
    this.save();
  }

  get isEmpty(): boolean {
    return Object.keys(this.serials).length === 0;
  }

  /** Called after every change; returns an unsubscribe function. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private load(): Record<string, string> {
    try {
      const raw = localStorage.getItem(this.storageKey);
      if (!raw) return {};
      const saved = JSON.parse(raw) as unknown;
      if (typeof saved !== 'object' || saved === null) return {};
      const valid: Record<string, string> = {};
      for (const [id, value] of Object.entries(saved)) {
        const serial = typeof value === 'string' ? normaliseSerial(value) : '';
        if (serial) valid[id] = serial;
      }
      return valid;
    } catch {
      // Storage blocked (private mode, disabled site data) or corrupt JSON
      return {};
    }
  }

  private save(): void {
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(this.serials));
    } catch {
      // Not persisting is fine; the serials still show for this page view
    }
    for (const listener of this.listeners) listener();
  }
}
