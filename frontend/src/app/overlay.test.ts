// @vitest-environment jsdom
/**
 * Overlay route: each widget mounts without chrome on a read-only socket,
 * an unknown id gets a message and no socket, and the replay rebuilds the
 * widget's state on every (re)connect.
 *
 * Stubbed as in app-smoke.test.ts: WebSocket (a fake the test drives),
 * fetch (canned empty responses), canvas getContext (inert), ResizeObserver,
 * timers and Date. Each test loads a private copy of the app modules
 * (vi.resetModules), since popovers and tooltips are module singletons and
 * isolate: false shares the module cache with other files.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import fc from 'fast-check';
import type { TelemetryRecord } from '../core/types';
import { OVERLAY_WIDGETS } from '../domain/overlay/widgets';
import { SERIES_OPTIONS } from '../features/graphs/panels';

const sockets: FakeWebSocket[] = [];

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;
  readyState = FakeWebSocket.CONNECTING;
  binaryType = 'blob';
  bufferedAmount = 0;
  extensions = '';
  protocol = '';
  onopen = null;
  onclose = null;
  onerror = null;
  onmessage = null;
  readonly sent: unknown[] = [];
  private readonly listeners = new Map<string, Set<(event: unknown) => void>>();
  constructor(readonly url: string, readonly protocols?: string | string[]) {
    sockets.push(this);
  }
  addEventListener(type: string, listener: (event: unknown) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }
  removeEventListener(type: string, listener: (event: unknown) => void): void {
    this.listeners.get(type)?.delete(listener);
  }
  dispatchEvent(): boolean {
    return true;
  }
  send(data: unknown): void {
    this.sent.push(data);
  }
  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }
  emit(type: string, event: Record<string, unknown> = {}): void {
    if (type === 'open') this.readyState = FakeWebSocket.OPEN;
    for (const listener of [...(this.listeners.get(type) ?? [])]) listener({ type, ...event });
  }
  message(data: unknown): void {
    this.emit('message', { data: JSON.stringify(data) });
  }
}

const requests: { url: string; method: string }[] = [];
/** What the fake backend answers for the timeline; null is a 404. */
let timelineStatusBody: unknown = null;
let missionFileBody: unknown = null;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const fakeFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  requests.push({ url, method: (init?.method ?? 'GET').toUpperCase() });
  if (url.startsWith('https://content.spacex.com/')) return json({});
  if (url === '/api/previous-flights' || url === '/api/trajectories' || url === '/api/missions') return json([]);
  if (url === '/api/timeline/status' && timelineStatusBody) return json(timelineStatusBody);
  if (url === '/api/missions/ift-14' && missionFileBody) return json(missionFileBody);
  return json({ detail: 'Not Found' }, 404);
});

function fakeContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const values: Record<PropertyKey, unknown> = {
    canvas,
    measureText: (text: string) => ({ width: String(text).length * 6, actualBoundingBoxAscent: 8, actualBoundingBoxDescent: 2 }),
    createLinearGradient: () => ({ addColorStop() {} }),
    createRadialGradient: () => ({ addColorStop() {} }),
    createPattern: () => ({}),
    getImageData: (_x: number, _y: number, w: number, h: number) => ({ width: w, height: h, data: new Uint8ClampedArray(Math.max(0, w * h * 4)) }),
    getLineDash: () => [],
    getTransform: () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }),
    isPointInPath: () => false,
    isPointInStroke: () => false,
  };
  const noop = () => undefined;
  return new Proxy(values, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (typeof prop === 'symbol') return undefined;
      return noop;
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

class FakeResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(0);
}

function record(seq: number, overrides: Partial<TelemetryRecord> = {}): TelemetryRecord {
  const s = 90 + seq;
  return {
    sequence_number: seq,
    mission_elapsed_time: `T+00:${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`,
    mission_elapsed_time_raw: null,
    speed_left: { value: 1000 + seq, unit: 'KM/H', status: 'available' },
    speed_right: { value: 900 + seq, unit: 'KM/H', status: 'available' },
    altitude_left: { value: 40, unit: 'KM', status: 'available' },
    altitude_right: { value: 41, unit: 'KM', status: 'available' },
    stage_left_label: null,
    stage_right_label: null,
    stage_separation_text: null,
    stage_assignment_left: 'super_heavy',
    stage_assignment_right: 'starship',
    separation_state: 'pre_separation',
    t_zero_found: true,
    stage_sep_found: false,
    starship_engines: {},
    superheavy_engines: {},
    detection_accuracy: { starship: 0, superheavy: 0 },
    timestamp: 1700000000000 + seq * 1000,
    ...overrides,
  };
}

function replay(chunk: number, records: TelemetryRecord[], final = true) {
  return { type: 'replay', payload: { run_id: 'run-1', chunk, final, truncated: false, records } };
}

type OverlayModule = typeof import('./overlay');
type SnapshotModule = typeof import('../domain/overlay/snapshot');
let overlay: OverlayModule;
let snapshotModule: SnapshotModule;
let app: HTMLElement;
let getContextSpy: ReturnType<typeof vi.spyOn>;

beforeAll(() => {
  vi.useFakeTimers({ now: new Date('2026-01-01T12:00:00.000Z') });
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.stubGlobal('fetch', fakeFetch);
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  getContextSpy = vi
    .spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockImplementation(function (this: HTMLCanvasElement) {
      return fakeContext(this);
    } as never);
});

afterAll(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  getContextSpy.mockRestore();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.body.innerHTML = '';
  document.documentElement.classList.remove('overlay');
  vi.resetModules();
});

beforeEach(async () => {
  sockets.length = 0;
  requests.length = 0;
  timelineStatusBody = null;
  missionFileBody = null;
  localStorage.clear();
  document.body.innerHTML = '<div id="app"></div>';
  app = document.getElementById('app')!;
  vi.resetModules();
  overlay = await import('./overlay');
  snapshotModule = await import('../domain/overlay/snapshot');
}, 60_000); // a cold transform of the widgets (chart.js, world-atlas) can take >10 s on a slow box

afterEach(() => {
  vi.clearAllTimers();
  document.documentElement.classList.remove('overlay');
});

describe('mountOverlay', () => {
  it.each(OVERLAY_WIDGETS.map((w) => w.id))('mounts %s without chrome on a read-only socket', async (id) => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const mounted = overlay.mountOverlay(app, { widget: id, snapshot: { v: 1 } });
    await settle();

    expect(mounted).not.toBeNull();
    expect(document.documentElement.classList.contains('overlay')).toBe(true);
    const widget = app.querySelector('#overlay-widget')!;
    expect(widget.childElementCount).toBeGreaterThan(0);
    expect(app.querySelectorAll('button, select, input')).toHaveLength(0);
    expect(sockets).toHaveLength(1);

    sockets[0].emit('open');
    sockets[0].message(replay(0, [record(1)]));
    mounted!.socket.sendCommand({ action: 'stop' });
    await settle();
    expect(sockets[0].sent).toEqual([]);
    expect(requests.filter((r) => r.method !== 'GET')).toEqual([]);
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it('shows the valid ids for an unknown overlay and opens no socket', async () => {
    expect(overlay.mountOverlay(app, { widget: 'nope', snapshot: { v: 1 } })).toBeNull();
    await settle();
    expect(app.textContent).toContain('Unknown overlay "nope"');
    for (const { id } of OVERLAY_WIDGETS) expect(app.textContent).toContain(id);
    expect(sockets).toHaveLength(0);
  });

  it('shows the replayed history and then live telemetry', async () => {
    overlay.mountOverlay(app, { widget: 'met', snapshot: { v: 1 } });
    await settle();
    const value = () => app.querySelector('.met-display__value')!.textContent;
    expect(value()).toBe('--:--:--');

    sockets[0].emit('open');
    sockets[0].message(replay(0, [record(1), record(2)], false));
    sockets[0].message(replay(1, [record(3)]));
    expect(value()).toBe('T+00:01:33');

    sockets[0].message({ type: 'telemetry', payload: record(4) });
    expect(value()).toBe('T+00:01:34');
  });

  it('rebuilds its state from the new replay after a reconnect, without duplicates', async () => {
    const mounted = overlay.mountOverlay(app, { widget: 'telemetry', snapshot: { v: 1 } })!;
    await settle();
    sockets[0].emit('open');
    sockets[0].message(replay(0, [record(1), record(2)]));
    sockets[0].message({ type: 'telemetry', payload: record(3) });

    sockets[0].emit('close', { code: 1006 });
    // Until the reconnect, not past its 4 s connection timeout
    for (let i = 0; i < 100 && sockets.length < 2; i++) await vi.advanceTimersByTimeAsync(100);
    expect(sockets).toHaveLength(2);
    sockets[1].emit('open');
    sockets[1].message(replay(0, [record(1), record(2), record(3)]));
    sockets[1].message({ type: 'telemetry', payload: record(4) });

    const seqs = mounted.stateManager.getState().telemetryRecords.map((r) => r.sequence_number);
    expect(seqs).toEqual([1, 2, 3, 4]);
    expect(app.querySelector('.telemetry-display__met-value')!.textContent).toBe('T+00:01:34');
  });

  it('puts the snapshot serials on the engine circles, not the stored ones', async () => {
    localStorage.setItem('engine-serials.starship', JSON.stringify({ ss_e6: 'R999' }));
    const getItem = vi.spyOn(Storage.prototype, 'getItem');
    overlay.mountOverlay(app, {
      widget: 'engines-starship',
      snapshot: { v: 1, m: { n: 'IFT 14', ss: 'S41', sh: 'B15' }, e: { '6': 'R576' } },
    });
    await settle();

    const serials = [...app.querySelectorAll<SVGGElement>('g[data-serial]')].map((g) => g.dataset.serial);
    expect(serials).toEqual(['R576']);
    expect(getItem.mock.calls.map((c) => c[0]).filter((k) => /^(engine-serials|engine-mission|flight-colors)/.test(k))).toEqual([]);
    getItem.mockRestore();
  });

  describe('timeline widgets', () => {
    const planned = [
      { id: 'liftoff', t: 0, event: 'Liftoff', vehicle: 'both', auto: { kind: 'clock' } },
      { id: 'maxq', t: 120, event: 'Max Q', vehicle: 'starship', auto: { kind: 'manual' } },
    ];
    const status = (id: string, value: string, source = 'auto') => ({
      event_id: id,
      status: value,
      source,
      evidence: id === 'liftoff' ? 'Liftoff at T+00:00:00' : null,
      met: null,
      updated_at: '2026-10-24T00:00:00Z',
    });
    const statusPayload = (revision: number, events: unknown[], extra: Record<string, unknown> = {}) => ({
      mission_id: 'ift-14',
      run_id: 'run-1',
      revision,
      met: 30,
      clock_source: 'telemetry',
      pending: 1,
      events,
      ...extra,
    });

    it('renders the full list from the status snapshot and the mission file, read-only', async () => {
      timelineStatusBody = statusPayload(1, [status('liftoff', 'success'), status('maxq', 'pending')]);
      missionFileBody = { id: 'ift-14', mission: 'IFT 14', ship: '', booster: '', starship_engines: {}, updated_at: null, timeline: planned };
      overlay.mountOverlay(app, { widget: 'timeline', snapshot: { v: 1 } });
      await settle();

      const rows = [...app.querySelectorAll<HTMLElement>('.timeline-row')];
      expect(rows.map((r) => r.dataset.eventId)).toEqual(['liftoff', 'maxq']);
      expect(rows.map((r) => r.dataset.status)).toEqual(['success', 'pending']);
      expect(app.querySelector('.timeline-panel__clock')!.textContent).toBe('T+00:00:30');
      expect(app.querySelector('.timeline-panel__pending')!.textContent).toBe('1 needs confirmation');
      expect(app.querySelectorAll('button, select, input')).toHaveLength(0);
      expect(requests.map((r) => r.url)).toEqual(expect.arrayContaining(['/api/timeline/status', '/api/missions/ift-14']));
      expect(requests.filter((r) => r.method !== 'GET')).toEqual([]);

      // A snapshot over the socket updates it, the page reading no mission file again
      sockets[0].emit('open');
      sockets[0].message({ type: 'timeline_status', payload: statusPayload(2, [status('liftoff', 'success'), status('maxq', 'failure', 'human')], { pending: 0 }) });
      expect([...app.querySelectorAll<HTMLElement>('.timeline-row')].map((r) => r.dataset.status)).toEqual(['success', 'failure']);
      expect(app.querySelector('.timeline-circle__badge')).not.toBeNull();
      expect(requests.filter((r) => r.url === '/api/missions/ift-14')).toHaveLength(1);
    });

    it('renders the current and next event from the replayed MET in the ticker', async () => {
      timelineStatusBody = statusPayload(1, [status('liftoff', 'success'), status('maxq', 'not_yet')], { met: null });
      missionFileBody = { id: 'ift-14', mission: 'IFT 14', ship: '', booster: '', starship_engines: {}, updated_at: null, timeline: planned };
      overlay.mountOverlay(app, { widget: 'timeline-next', snapshot: { v: 1 } });
      await settle();
      sockets[0].emit('open');
      sockets[0].message(replay(0, [record(1)]));

      expect([...app.querySelectorAll<HTMLElement>('.timeline-row')].map((r) => r.dataset.eventId)).toEqual(['liftoff', 'maxq']);
      expect(app.querySelector('.timeline-row--current')!.getAttribute('data-event-id')).toBe('liftoff');
      expect(app.querySelector('.timeline-row__eta:not(:empty)')!.textContent).toBe('in 0:29');
      expect(app.querySelectorAll('button, select, input')).toHaveLength(0);
    });

    it('shows the empty state when no timeline is followed', async () => {
      timelineStatusBody = statusPayload(1, [], { mission_id: null, pending: 0 });
      overlay.mountOverlay(app, { widget: 'timeline', snapshot: { v: 1 } });
      await settle();
      expect(app.querySelector<HTMLElement>('.timeline-panel__empty')!.hidden).toBe(false);
      expect(app.querySelectorAll('.timeline-row')).toHaveLength(0);
      expect(requests.some((r) => r.url.startsWith('/api/missions/'))).toBe(false);
    });

    it('does not load the timeline for widgets that do not show it', async () => {
      overlay.mountOverlay(app, { widget: 'met', snapshot: { v: 1 } });
      await settle();
      expect(requests.some((r) => r.url.includes('timeline'))).toBe(false);
    });
  });

  it('is what main.ts mounts for ?overlay=', async () => {
    const { encodeSnapshot } = snapshotModule;
    window.history.replaceState({}, '', `/?overlay=met&s=${encodeSnapshot({ v: 1 })}`);
    try {
      vi.resetModules();
      await import('./main');
      await settle();
      expect(app.querySelector('.met-display')).not.toBeNull();
      expect(app.querySelector('.dashboard-header')).toBeNull();
    } finally {
      window.history.replaceState({}, '', '/');
    }
  }, 60_000);
});

describe('overlay widget ids', () => {
  it('has a graph widget for every graph series', () => {
    const graphIds = OVERLAY_WIDGETS.map((w) => w.id).filter((id) => id.startsWith('graph-'));
    expect(graphIds).toEqual(SERIES_OPTIONS.map((o) => `graph-${o.key}`));
  });
});

describe('overlay properties', () => {
  const widgetIds = OVERLAY_WIDGETS.map((w) => w.id);
  const seededKeys = [
    'engine-serials.starship',
    'engine-serials.superheavy',
    'flight-colors.telemetry',
    'flight-colors.trajectory',
    'engine-mission.current',
    'time-series-graphs.export-options',
  ];
  const hex = fc.array(fc.constantFrom(...'0123456789abcdef'), { minLength: 6, maxLength: 6 }).map((d) => `#${d.join('')}`);
  const snapshotArb = fc.record(
    {
      v: fc.constant(1 as const),
      x: fc.constant([['ift-12.json', '#e91e63']] as [string, string][]),
      t: fc.dictionary(fc.constantFrom('ship33', 'ship39'), hex),
      h: fc.subarray(['ship33', 'ship39']),
      m: fc.constant({ n: 'IFT 14', ss: 'S41', sh: 'B15' }),
      e: fc.dictionary(fc.integer({ min: 1, max: 33 }).map(String), fc.constantFrom('R576', 'RVAC-1')),
    },
    { requiredKeys: ['v'] },
  );

  // Property 14
  it('is read-only and isolated for every widget id and snapshot', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem');
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem');
    try {
      await fc.assert(
        fc.asyncProperty(fc.constantFrom(...widgetIds), snapshotArb, async (id, snapshot) => {
          sockets.length = 0;
          requests.length = 0;
          getItem.mockClear();
          setItem.mockClear();
          removeItem.mockClear();
          app.innerHTML = '';
          for (const key of seededKeys) localStorage.setItem(key, JSON.stringify({ ss_e6: 'R999', sh_e1: 'R888', seeded: '#123456' }));
          setItem.mockClear();

          const mounted = overlay.mountOverlay(app, { widget: id, snapshot })!;
          await settle();
          expect(sockets).toHaveLength(1);
          sockets[0].emit('open');
          sockets[0].message(replay(0, [record(1), record(2)], false));
          sockets[0].message(replay(1, [record(3)]));
          sockets[0].message({ type: 'telemetry', payload: record(4) });
          mounted.socket.sendCommand({ action: 'stop' });
          await settle();

          expect(sockets[0].sent).toEqual([]);
          expect(requests.filter((r) => r.method !== 'GET')).toEqual([]);
          expect(setItem).not.toHaveBeenCalled();
          expect(removeItem).not.toHaveBeenCalled();
          expect(getItem.mock.calls.map((c) => c[0]).filter((k) => k !== 'LOG_LEVEL')).toEqual([]);
          expect(app.querySelectorAll('button, select, input')).toHaveLength(0);
        }),
        { numRuns: 25 },
      );
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
      removeItem.mockRestore();
    }
  }, 60_000);

  it('shows the list of valid ids for any unknown id and opens no socket', () => {
    fc.assert(
      fc.property(
        fc.string({ maxLength: 20 }).filter((t) => !widgetIds.includes(t as never)),
        (id) => {
          sockets.length = 0;
          app.innerHTML = '';
          expect(overlay.mountOverlay(app, { widget: id, snapshot: { v: 1 } })).toBeNull();
          for (const valid of widgetIds) expect(app.textContent).toContain(valid);
          expect(app.querySelector('#overlay-widget')).toBeNull();
          expect(sockets).toHaveLength(0);
        },
      ),
      { numRuns: 50 },
    );
  });

  // Property 15
  it('has a registry entry for every widget id and a graph widget for every series', async () => {
    const { OVERLAY_REGISTRY } = await import('./overlay-registry');
    expect(Object.keys(OVERLAY_REGISTRY).sort()).toEqual([...widgetIds].sort());
    for (const { key } of SERIES_OPTIONS) {
      expect(widgetIds).toContain(`graph-${key}`);
      expect(OVERLAY_REGISTRY[`graph-${key}` as keyof typeof OVERLAY_REGISTRY]).toBeTypeOf('function');
    }
  });
});
