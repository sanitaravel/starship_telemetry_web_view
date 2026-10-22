// @vitest-environment jsdom
/**
 * Whole-dashboard smoke test: mounts the real app the way the browser does
 * (by importing the entry module) and snapshots the resulting DOM. Moving
 * files between layers must leave this snapshot unchanged; if main.ts moves,
 * update APP_ENTRY below and nothing else.
 *
 * Stubbed: WebSocket (never connects), fetch (canned empty responses, see
 * respond()), canvas getContext (inert 2D context so Chart.js initialises),
 * ResizeObserver, timers and Date (fake, fixed clock). localStorage starts
 * and ends empty. The module cache is reset before and after (isolate: false
 * would otherwise mix these real modules with other files' vi.mock ones).
 *
 * Not covered: canvas pixels (the globe and charts draw into a stub context;
 * jsdom has zero layout sizes, so the globe never draws at all), CSS, and
 * anything that needs a live backend message or a tracker sample.
 */
import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';

const APP_ENTRY = './main';

const FIXED_NOW = new Date('2026-01-01T12:00:00.000Z');

const TRACKER_PREFIX = 'https://content.spacex.com/cms-assets/starship_tracker_public.json';

/** Every URL fetched, in request order, with the tracker's cache-buster removed. */
const requested: string[] = [];
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
  constructor(readonly url: string, readonly protocols?: string | string[]) {
    sockets.push(this);
  }
  addEventListener(): void {}
  removeEventListener(): void {}
  dispatchEvent(): boolean {
    return true;
  }
  send(): void {}
  close(): void {}
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Canned backend: nothing recorded, no saved missions, no live ship. */
function respond(url: string): Response {
  if (url.startsWith(TRACKER_PREFIX)) return json({});
  switch (url) {
    case '/api/status':
      return json({
        status: 'idle',
        gpu: { available: false, device_name: null },
        skip_frames: 30,
        current_sequence: 0,
        version: '0.0.0.0',
      });
    case '/api/missions':
    case '/api/previous-flights':
    case '/api/trajectories':
      return json([]);
    default:
      return json({ detail: 'Not Found' }, 404);
  }
}

const fakeFetch = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  const clean = url.startsWith(TRACKER_PREFIX) ? TRACKER_PREFIX : url;
  requested.push(clean);
  return respond(clean);
});

/**
 * An inert CanvasRenderingContext2D: every method is a no-op, apart from the
 * few whose return value callers read (measureText, gradients, image data).
 */
function fakeContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const values: Record<PropertyKey, unknown> = {
    canvas,
    measureText: (text: string) => ({
      width: String(text).length * 6,
      actualBoundingBoxAscent: 8,
      actualBoundingBoxDescent: 2,
    }),
    createLinearGradient: () => ({ addColorStop() {} }),
    createRadialGradient: () => ({ addColorStop() {} }),
    createPattern: () => ({}),
    getImageData: (_x: number, _y: number, w: number, h: number) => ({
      width: w,
      height: h,
      data: new Uint8ClampedArray(Math.max(0, w * h * 4)),
    }),
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

/** Lets pending fetches, promise chains and zero-delay timers / frames run. */
async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersToNextFrame();
  for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(0);
}

/**
 * Indent the markup one element per line so snapshot diffs are readable.
 * Text and attributes are kept verbatim; only whitespace between tags changes.
 */
function pretty(root: Element): string {
  const lines: string[] = [];
  const walk = (node: Node, depth: number): void => {
    const pad = '  '.repeat(depth);
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent?.trim();
      if (text) lines.push(pad + text.replace(/\s+/g, ' '));
      return;
    }
    if (node.nodeType === Node.COMMENT_NODE) {
      lines.push(`${pad}<!--${node.textContent}-->`);
      return;
    }
    if (!(node instanceof Element)) return;
    const tag = node.tagName.toLowerCase();
    let attrs = [...node.attributes].map((a) => ` ${a.name}="${a.value.replace(/"/g, '&quot;')}"`).join('');
    // Form state set through properties is not in the attributes; show it as pseudo-attributes.
    if (node instanceof HTMLInputElement) {
      attrs +=
        node.type === 'checkbox' || node.type === 'radio'
          ? ` .checked="${node.checked}"`
          : ` .value="${node.value}"`;
    } else if (node instanceof HTMLSelectElement || node instanceof HTMLTextAreaElement) {
      attrs += ` .value="${node.value}"`;
    }
    const isVoid = /^(area|br|col|embed|hr|img|input|link|meta|source|track|wbr)$/.test(tag);
    if (isVoid) {
      lines.push(`${pad}<${tag}${attrs}>`);
      return;
    }
    const children = [...node.childNodes].filter(
      (c) => c.nodeType !== Node.TEXT_NODE || (c.textContent ?? '').trim() !== '',
    );
    if (children.length === 0) {
      lines.push(`${pad}<${tag}${attrs}></${tag}>`);
      return;
    }
    lines.push(`${pad}<${tag}${attrs}>`);
    for (const child of children) walk(child, depth + 1);
    lines.push(`${pad}</${tag}>`);
  };
  for (const child of root.childNodes) walk(child, 0);
  return normalise(lines.join('\n'));
}

/**
 * Element ids built from module-level counters (ExportMenu, MissionFields)
 * depend on how many instances earlier test files in the same worker made
 * (isolate: false), so the number is replaced with '#'. Nothing else is
 * stripped.
 */
function normalise(html: string): string {
  return html.replace(/\b(time-series-graphs-export-|mission-suggestions-)\d+\b/g, '$1#');
}

const SECTION_IDS = [
  'pipeline-controls',
  'telemetry-downloads',
  'telemetry-display',
  'frame-preview',
  'engine-visualizer',
  'ship-globe',
  'time-series-graphs',
  'backend-version',
];

describe('app smoke test', () => {
  let getContextSpy: ReturnType<typeof vi.spyOn>;

  beforeAll(async () => {
    vi.useFakeTimers({ now: FIXED_NOW });
    localStorage.clear();
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('fetch', fakeFetch);
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    getContextSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation(function (this: HTMLCanvasElement) {
        return fakeContext(this);
      } as never);

    document.body.innerHTML = '<div id="app"></div>';
    // isolate: false shares the module cache between test files in a worker:
    // drop modules other files loaded (possibly through their vi.mock
    // factories) so the app is built from the real ones.
    vi.resetModules();
    await import(/* @vite-ignore */ APP_ENTRY);
    await settle();
  }, 60_000); // a cold transform of the whole app (chart.js, world-atlas) can take >10 s on a slow box

  afterAll(() => {
    vi.clearAllTimers(); // tracker polling, websocket reconnects, save debounces
    vi.useRealTimers();
    getContextSpy.mockRestore();
    vi.unstubAllGlobals();
    localStorage.clear();
    document.body.innerHTML = '';
    // Don't hand the real app modules to later files that vi.mock some of them.
    vi.resetModules();
  });

  it('opens one websocket to the telemetry endpoint', () => {
    expect(sockets.map((s) => s.url)).toEqual([`ws://${window.location.host}/ws/telemetry`]);
  });

  it('requests the expected backend endpoints', () => {
    expect([...new Set(requested)].sort()).toEqual(
      [
        '/api/previous-flights',
        '/api/status',
        '/api/trajectories',
        TRACKER_PREFIX,
      ].sort(),
    );
  });

  it('mounts every section', () => {
    for (const id of SECTION_IDS) {
      const el = document.getElementById(id);
      expect(el, `#${id} missing`).not.toBeNull();
      const mounted = el!.childElementCount > 0 || (el!.textContent ?? '').trim() !== '';
      expect(mounted, `#${id} is empty`).toBe(true);
    }
  });

  it('renders the same DOM as before', () => {
    expect(pretty(document.body)).toMatchSnapshot();
  });
});
