// @vitest-environment jsdom
/**
 * Canvas-trace equivalence test for ShipGlobe. jsdom has no layout, so the
 * app smoke test never draws the globe; this test forces a real draw and
 * records every 2D-context call and property set, with numbers rounded to
 * 6 decimals, plus the host DOM after each step of a fixed scenario.
 *
 * The snapshot was produced from the pre-split single-file globe (HEAD before
 * the features/ move). Any change in what the globe paints, how often it asks
 * for a frame, or what its overlay DOM contains shows up as a snapshot diff.
 *
 * Stubbed: canvas getContext (recording Proxy, measureText is 6.25 px per
 * character), clientWidth/clientHeight of the host and canvas wrap (600 px,
 * then 760 px after the window resize), getBoundingClientRect (fixed boxes
 * for the canvas and the zoom controls), pointer capture (jsdom lacks it),
 * ResizeObserver (fired by hand), requestAnimationFrame (queue flushed per
 * step), devicePixelRatio (2) and timers / Date (fake, fixed clock).
 * Restored after the test: isolate:false means other files share this worker.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ShipGlobe } from './globe';
import { FlightColors } from '../../state/flight-colors';
import type { ShipPosition, ShipTrack } from '../../core/ship-track';
import type { ShipTracker } from '../../state/ship-tracker';
import type { HistoricalTrajectory } from '../../api/trajectories';

const FIXED_NOW = new Date('2026-01-01T12:00:00.000Z');
const WRAP_WIDTH_BEFORE = 600;
const WRAP_WIDTH_AFTER = 760;

/** Every canvas call, property set, pointer-capture call and frame request, in order. */
let trace: string[] = [];

/** Numbers rounded to 6 decimals: float noise is ignored, any real move is not. */
function fmt(value: unknown): string {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? String(Number(value.toFixed(6))) : String(value);
  }
  if (typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(fmt).join(',')}]`;
  if (value === null || value === undefined || typeof value === 'boolean') return String(value);
  return Object.prototype.toString.call(value);
}

/** A 2D context that logs every method call and property set. */
function recordingContext(): CanvasRenderingContext2D {
  const props = new Map<string, unknown>();
  const ctx = new Proxy({} as Record<string, unknown>, {
    get(_target, prop) {
      if (typeof prop !== 'string') return undefined;
      if (props.has(prop)) return props.get(prop);
      if (prop === 'measureText') {
        return (text: string) => {
          const width = text.length * 6.25;
          trace.push(`measureText(${fmt(text)}) -> ${fmt(width)}`);
          return { width };
        };
      }
      return (...args: unknown[]) => {
        trace.push(`${prop}(${args.map(fmt).join(',')})`);
      };
    },
    set(_target, prop, value) {
      if (typeof prop === 'string') {
        props.set(prop, value);
        trace.push(`${prop}=${fmt(value)}`);
      }
      return true;
    },
  });
  return ctx as unknown as CanvasRenderingContext2D;
}

/** Replaces a property for the duration of the file; restored in afterAll. */
const restores: Array<() => void> = [];
function defineTemp(target: object, key: string, descriptor: PropertyDescriptor): void {
  const original = Object.getOwnPropertyDescriptor(target, key);
  Object.defineProperty(target, key, { configurable: true, ...descriptor });
  restores.push(() => {
    if (original) Object.defineProperty(target, key, original);
    else Reflect.deleteProperty(target, key);
  });
}

let wrapWidth = WRAP_WIDTH_BEFORE;
const frameQueue: FrameRequestCallback[] = [];
const resizeObservers: FakeResizeObserver[] = [];

class FakeResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {
    resizeObservers.push(this);
  }
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  fire(): void {
    this.callback([], this as unknown as ResizeObserver);
  }
}

/** Runs queued animation frames (and any due timers) until none are left; returns how many ran. */
function flush(): number {
  let ran = 0;
  while (frameQueue.length > 0) {
    const batch = frameQueue.splice(0);
    for (const callback of batch) {
      ran++;
      trace.push('--frame--');
      callback(0);
    }
  }
  vi.advanceTimersByTime(0);
  return ran;
}

function fakeTracker(initial: ShipTrack[]) {
  let current = initial;
  const listeners: Array<(tracks: ShipTrack[]) => void> = [];
  const tracker = {
    subscribe(listener: (tracks: ShipTrack[]) => void) {
      listeners.push(listener);
      listener(current);
      return () => {};
    },
  };
  return {
    tracker: tracker as unknown as ShipTracker,
    publish(tracks: ShipTrack[]) {
      current = tracks;
      for (const listener of listeners) listener(tracks);
    },
  };
}

function samples(start: number, points: [number, number][], altitude: number, speed: number): ShipPosition[] {
  return points.map(([longitude, latitude], i) => ({
    gpsTime: start + i * 2,
    missionTime: i * 2,
    altitude: altitude + i * 1000,
    speed: speed - i * 10,
    latitude,
    longitude,
  }));
}

const SHIP_39_PATH: [number, number][] = [
  [-97.15, 25.99],
  [-96.2, 26.8],
  [-94.6, 28.1],
  [-92.1, 29.5],
  [-89.0, 30.4],
];
const SHIP_40_PATH: [number, number][] = [
  [-80.5, 27.0],
  [-79.0, 28.5],
  [-77.2, 30.1],
  [-75.0, 31.6],
];

function initialTracks(): ShipTrack[] {
  return [
    { id: 'ship39', number: 39, positions: samples(1000, SHIP_39_PATH, 50000, 2500) },
    { id: 'ship40', number: 40, positions: samples(1004, SHIP_40_PATH, 30000, 1800) },
  ];
}

function withNewSample(tracks: ShipTrack[]): ShipTrack[] {
  const [first, second] = tracks;
  const last = second.positions[second.positions.length - 1];
  const next: ShipPosition = {
    gpsTime: last.gpsTime + 2,
    missionTime: last.missionTime + 2,
    altitude: last.altitude + 1500,
    speed: last.speed - 5,
    latitude: last.latitude + 0.6,
    longitude: last.longitude + 1.1,
  };
  return [first, { ...second, positions: [...second.positions, next] }];
}

function trajectory(name: string, number: number, start: [number, number], steps: number): HistoricalTrajectory {
  const coordinates: [number, number][] = Array.from({ length: steps }, (_, i) => [
    start[0] + i * 0.9,
    start[1] + i * 0.45,
  ]);
  return { name, number, label: `Ship ${number}`, color: number === 36 ? '#4fc3f7' : '#ba68c8', coordinates };
}

const HISTORY: HistoricalTrajectory[] = [
  trajectory('ship36', 36, [-100, 22], 14),
  trajectory('ship35', 35, [-60, 10], 10),
];

/**
 * What a step is compared on. The full call list is hashed rather than stored, so the
 * snapshot stays small; the hash covers every call, in order.
 *
 * To see where a mismatch starts, rerun with GLOBE_TRACE_DUMP=1. Each step's full trace
 * (one call per line) is then written to os.tmpdir() as globe-trace-<step>.txt, and the
 * old and new dumps can be diffed.
 */
interface StepRecord {
  frames: number;
  callCount: number;
  traceSha256: string;
  innerHTML: string;
}

/** sha256 of the canonical JSON (a JSON array of strings) of the full call list. */
function traceDigest(calls: string[]): string {
  return createHash('sha256').update(JSON.stringify(calls)).digest('hex');
}

describe('ShipGlobe canvas trace', () => {
  beforeAll(() => {
    vi.useFakeTimers({ now: FIXED_NOW, toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      trace.push('requestAnimationFrame');
      frameQueue.push(callback);
      return frameQueue.length;
    });
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    defineTemp(window, 'devicePixelRatio', { value: 2, writable: true });

    const contexts = new WeakMap<HTMLCanvasElement, CanvasRenderingContext2D>();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (
      this: HTMLCanvasElement,
      kind: string,
    ) {
      trace.push(`getContext(${fmt(kind)})`);
      let ctx = contexts.get(this);
      if (!ctx) {
        ctx = recordingContext();
        contexts.set(this, ctx);
      }
      return ctx;
    } as unknown as typeof HTMLCanvasElement.prototype.getContext);

    const captured = new Set<number>();
    defineTemp(HTMLElement.prototype, 'setPointerCapture', {
      value(this: HTMLElement, pointerId: number) {
        captured.add(pointerId);
        trace.push(`setPointerCapture(${pointerId})`);
      },
    });
    defineTemp(HTMLElement.prototype, 'releasePointerCapture', {
      value(this: HTMLElement, pointerId: number) {
        captured.delete(pointerId);
        trace.push(`releasePointerCapture(${pointerId})`);
      },
    });
    defineTemp(HTMLElement.prototype, 'hasPointerCapture', {
      value(this: HTMLElement, pointerId: number) {
        return captured.has(pointerId);
      },
    });

    // The host and the canvas wrap are the only elements the globe measures.
    const sized = (el: HTMLElement) => el.hasAttribute('data-globe-host') || el.classList.contains('ship-globe__canvas-wrap');
    defineTemp(HTMLElement.prototype, 'clientWidth', {
      get(this: HTMLElement) {
        return sized(this) ? wrapWidth : 0;
      },
    });
    defineTemp(HTMLElement.prototype, 'clientHeight', {
      get(this: HTMLElement) {
        return sized(this) ? wrapWidth : 0;
      },
    });
    defineTemp(HTMLElement.prototype, 'getBoundingClientRect', {
      value(this: HTMLElement) {
        const rect =
          this.classList.contains('ship-globe__zoom')
            ? { left: 500, top: 480, width: 100, height: 120 }
            : this.tagName === 'CANVAS'
              ? { left: 40, top: 60, width: wrapWidth, height: Math.min(wrapWidth, 640) }
              : { left: 0, top: 0, width: 0, height: 0 };
        return {
          ...rect,
          x: rect.left,
          y: rect.top,
          right: rect.left + rect.width,
          bottom: rect.top + rect.height,
          toJSON() {
            return rect;
          },
        };
      },
    });
  });

  afterAll(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    for (const restore of restores.reverse()) restore();
    restores.length = 0;
    vi.useRealTimers();
    localStorage.clear();
    document.body.replaceChildren();
  });

  it('draws the same calls and DOM at every step of the scenario', () => {
    const host = document.createElement('section');
    host.setAttribute('data-globe-host', '');
    // Attached, as in the app: jsdom fires checkbox change events only for connected inputs.
    document.body.append(host);
    const fake = fakeTracker(initialTracks());
    let globe: ShipGlobe | undefined;
    const canvas = () => host.querySelector<HTMLCanvasElement>('.ship-globe__canvas')!;
    const button = (selector: string) => host.querySelector<HTMLElement>(selector)!;
    const pointer = (type: string, x: number, y: number) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.assign(event, { pointerId: 1, clientX: x, clientY: y });
      canvas().dispatchEvent(event);
    };
    const wheel = (deltaY: number) => {
      const event = new Event('wheel', { bubbles: true, cancelable: true });
      Object.assign(event, { deltaY });
      canvas().dispatchEvent(event);
    };
    const zoom = (action: string) => button(`[data-zoom="${action}"]`).click();

    const steps: Array<[string, () => void]> = [
      [
        'mount, setHistory and initial draw',
        () => {
          globe = new ShipGlobe(host, fake.tracker);
          globe.setHistory(HISTORY);
        },
      ],
      [
        'pointer drag',
        () => {
          pointer('pointerdown', 300, 300);
          pointer('pointermove', 330, 310);
          pointer('pointermove', 372, 326);
          pointer('pointerup', 372, 326);
        },
      ],
      ['center on ship', () => button('.ship-globe__center-btn').click()],
      ['wheel zoom in', () => wheel(-240)],
      ['wheel zoom out', () => wheel(120)],
      ['zoom in button', () => zoom('in')],
      ['zoom out button', () => zoom('out')],
      ['reset zoom button', () => zoom('reset')],
      [
        'window resize',
        () => {
          wrapWidth = WRAP_WIDTH_AFTER;
          window.dispatchEvent(new Event('resize'));
          for (const observer of resizeObservers) observer.fire();
        },
      ],
      [
        'toggle ship36 path off',
        () => {
          const checkbox = host.querySelector<HTMLInputElement>('[data-trajectory="ship36"]')!;
          checkbox.click();
          expect(checkbox.checked).toBe(false);
        },
      ],
      ['new track sample', () => fake.publish(withNewSample(initialTracks()))],
    ];

    trace = [];
    const records: Array<[string, StepRecord]> = [];
    for (const [name, act] of steps) {
      act();
      const frames = flush();
      const calls = trace.splice(0);
      expect(calls.length, `${name} produced no trace`).toBeGreaterThan(0);
      if (process.env.GLOBE_TRACE_DUMP === '1') {
        const file = join(tmpdir(), `globe-trace-${records.length}.txt`);
        writeFileSync(file, calls.join('\n'));
      }
      records.push([name, { frames, callCount: calls.length, traceSha256: traceDigest(calls), innerHTML: host.innerHTML }]);
    }

    expect(globe).toBeDefined();
    records.forEach(([name, record], index) => {
      expect(record).toMatchSnapshot(`${index}. ${name}`);
    });
  });

  it('without chrome draws one hidden path less, a colour override and no controls', () => {
    wrapWidth = WRAP_WIDTH_BEFORE;
    const host = document.createElement('section');
    host.setAttribute('data-globe-host', '');
    document.body.append(host);
    const fake = fakeTracker(initialTracks());
    const colors = new FlightColors('trajectory', { persist: false, initial: { ship36: '#81c784' } });
    let globe: ShipGlobe | undefined;

    const steps: Array<[string, () => void]> = [
      [
        'mount without chrome, setHistory and initial draw',
        () => {
          globe = new ShipGlobe(host, fake.tracker, { chrome: false, hidden: ['ship35'], colors });
          globe.setHistory(HISTORY);
        },
      ],
      ['new track sample', () => fake.publish(withNewSample(initialTracks()))],
    ];

    trace = [];
    const records: Array<[string, StepRecord]> = [];
    for (const [name, act] of steps) {
      act();
      const frames = flush();
      const calls = trace.splice(0);
      expect(calls.length, `${name} produced no trace`).toBeGreaterThan(0);
      records.push([name, { frames, callCount: calls.length, traceSha256: traceDigest(calls), innerHTML: host.innerHTML }]);
    }

    expect(host.querySelectorAll('button, select, input')).toHaveLength(0);
    expect(host.querySelector('.ship-globe__readout')?.textContent).toContain('Ship 40');
    expect(globe!.overlaySnapshot()).toEqual({ v: 1, t: { ship36: '#81c784' }, h: ['ship35'] });
    records.forEach(([name, record], index) => {
      expect(record).toMatchSnapshot(`chrome off ${index}. ${name}`);
    });
    host.remove();
  });
});
