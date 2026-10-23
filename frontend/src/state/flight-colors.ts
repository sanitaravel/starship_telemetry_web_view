/** Colour of the live flight on every view; previous flights should stay clear of it. */
export const LIVE_COLOR = '#FF8014';

/** Quick picks offered in the colour popover, all readable on the dark panels. */
export const PRESET_COLORS = [
  '#4fc3f7',
  '#00bcd4',
  '#4db6ac',
  '#81c784',
  '#dce775',
  '#ffeb3b',
  '#e91e63',
  '#f06292',
  '#ba68c8',
  '#9fa8da',
  '#90a4ae',
  '#e0e0e0',
];

const HEX_COLOR = /^#[0-9a-f]{6}$/i;

/** RGB distance below which a colour reads as the live orange. */
const LIVE_COLOR_MIN_DISTANCE = 90;

function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** True when `hex` is close enough to the live orange to be mistaken for it. */
export function isNearLiveColor(hex: string): boolean {
  if (!HEX_COLOR.test(hex)) return false;
  const [r1, g1, b1] = rgb(hex);
  const [r2, g2, b2] = rgb(LIVE_COLOR);
  return Math.hypot(r1 - r2, g1 - g2, b1 - b2) < LIVE_COLOR_MIN_DISTANCE;
}

/**
 * User-chosen colours for previous flights, keyed by flight id and saved in
 * localStorage. Only overrides are stored; callers pass their own default.
 * Each view has its own namespace, so a telemetry flight and a globe
 * trajectory never share a setting.
 */
export class FlightColors {
  private readonly storageKey: string;
  private readonly persist: boolean;
  private overrides: Record<string, string>;
  private listeners = new Set<() => void>();

  constructor(namespace: string, options: FlightColorsOptions = {}) {
    this.storageKey = `flight-colors.${namespace}`;
    this.persist = options.persist ?? true;
    this.overrides = this.persist ? this.load() : validColors(options.initial ?? {});
  }

  /** Every override set, by flight id. */
  snapshot(): Record<string, string> {
    return { ...this.overrides };
  }

  get(id: string, fallback: string): string {
    return this.overrides[id] ?? fallback;
  }

  isCustom(id: string): boolean {
    return id in this.overrides;
  }

  set(id: string, hex: string): void {
    if (!HEX_COLOR.test(hex) || this.overrides[id] === hex.toLowerCase()) return;
    this.overrides = { ...this.overrides, [id]: hex.toLowerCase() };
    this.save();
  }

  reset(id: string): void {
    if (!(id in this.overrides)) return;
    const { [id]: _removed, ...rest } = this.overrides;
    this.overrides = rest;
    this.save();
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
      return validColors(JSON.parse(raw) as unknown);
    } catch {
      // Storage blocked (private mode, disabled site data) or corrupt JSON
      return {};
    }
  }

  private save(): void {
    if (this.persist) {
      try {
        localStorage.setItem(this.storageKey, JSON.stringify(this.overrides));
      } catch {
        // Not persisting is fine; the colour still applies for this page view
      }
    }
    for (const listener of this.listeners) listener();
  }
}

export interface FlightColorsOptions {
  /**
   * False keeps the colours in memory only: localStorage is never read or
   * written (overlays). Default true.
   */
  persist?: boolean;
  /** Starting overrides when not persisting, validated like loaded ones. */
  initial?: Record<string, string>;
}

/** The valid hex colours of a loaded object, lower-cased, by flight id. */
function validColors(saved: unknown): Record<string, string> {
  if (typeof saved !== 'object' || saved === null) return {};
  const valid: Record<string, string> = {};
  for (const [id, hex] of Object.entries(saved)) {
    if (typeof hex === 'string' && HEX_COLOR.test(hex)) valid[id] = hex.toLowerCase();
  }
  return valid;
}
