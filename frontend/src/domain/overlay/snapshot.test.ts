import { describe, it, expect, vi, afterEach } from 'vitest';
import fc from 'fast-check';
import {
  buildOverlayUrl,
  decodeSnapshot,
  encodeSnapshot,
  MAX_COMPARED_FLIGHTS,
  MAX_ENGINE_NUMBER,
  MAX_FLIGHT_FILENAME,
  MAX_TRAJECTORIES,
  MAX_TRAJECTORY_NAME,
  MAX_MISSION_NAME,
  MAX_VEHICLE_NAME,
  parseOverlayLocation,
  type OverlaySnapshot,
} from './snapshot';
import { MAX_SERIAL_LENGTH } from '../engines/serials';
import { isOverlayWidgetId, OVERLAY_WIDGETS } from './widgets';

afterEach(() => vi.restoreAllMocks());

/** Spy on console.warn, where the logger writes warnings. */
function spyWarn() {
  return vi.spyOn(console, 'warn').mockImplementation(() => {});
}

describe('encodeSnapshot / decodeSnapshot', () => {
  it.each<[string, OverlaySnapshot]>([
    ['telemetry (none)', { v: 1 }],
    ['graph', { v: 1, x: [['ift-12.json', '#e91e63'], ['ift-13.json', '#00bcd4']] }],
    ['globe', { v: 1, t: { ship39: '#81c784' }, h: ['ship33'] }],
    ['engines', { v: 1, m: { n: 'IFT 14', ss: 'S41', sh: 'B15' }, e: { '1': 'R100', '33': 'R576' } }],
  ])('round-trips the %s snapshot', (_name, snapshot) => {
    const text = encodeSnapshot(snapshot);
    expect(text).toMatch(/^[A-Za-z0-9_-]*$/);
    expect(decodeSnapshot(text)).toEqual(snapshot);
  });

  it('round-trips a non-ASCII mission name', () => {
    const snapshot: OverlaySnapshot = { v: 1, m: { n: 'Полёт 14 🚀', ss: 'Корабль', sh: 'Ускоритель' }, e: {} };
    expect(decodeSnapshot(encodeSnapshot(snapshot))).toEqual(snapshot);
  });

  it('decodes the design example', () => {
    expect(decodeSnapshot('eyJ2IjoxLCJ4IjpbWyJpZnQtMTIuanNvbiIsIiNlOTFlNjMiXV19')).toEqual({
      v: 1,
      x: [['ift-12.json', '#e91e63']],
    });
  });

  it.each([
    ['missing', null],
    ['empty', ''],
    ['not base64url', 'a+b/c='],
    ['not JSON', encodeText('not json')],
    ['not an object', encodeText('[1,2]')],
    ['invalid UTF-8', btoa('\xff\xfe').replace(/=+$/, '')],
  ])('falls back to defaults with a warning when %s', (_name, text) => {
    const warn = spyWarn();
    expect(decodeSnapshot(text)).toEqual({ v: 1 });
    expect(warn).toHaveBeenCalled();
  });

  it('falls back to defaults with a warning on an unknown version', () => {
    const warn = spyWarn();
    expect(decodeSnapshot(encodeText('{"v":2,"x":[["ift-12.json","#e91e63"]]}'))).toEqual({ v: 1 });
    expect(warn).toHaveBeenCalled();
  });

  it('drops fields that do not validate', () => {
    const text = encodeText(
      JSON.stringify({
        v: 1,
        x: [['../etc/passwd', '#e91e63'], ['ift-12.json', 'red'], ['ift-13.json', '#00BCD4'], 'junk'],
        t: { 'bad name': '#000000', ship39: '#81c784', ship40: 5 },
        h: ['ship33', 7, 'ship33'],
        m: { n: 'A'.repeat(200), ss: 'S\u000041', sh: 9 },
        e: { '0': 'R1', '34': 'R2', '6': ' r576 ', x: 'R3', '7': '!!' },
      }),
    );
    expect(decodeSnapshot(text)).toEqual({
      v: 1,
      x: [['ift-13.json', '#00BCD4']],
      t: { ship39: '#81c784' },
      h: ['ship33'],
      m: { n: 'A'.repeat(MAX_MISSION_NAME), ss: 'S41', sh: '' },
      e: { '6': 'R576' },
    });
  });

  it('keeps at most MAX_COMPARED_FLIGHTS flights', () => {
    const x = Array.from({ length: 10 }, (_, i) => [`ift-${i}.json`, '#e91e63']);
    expect(decodeSnapshot(encodeText(JSON.stringify({ v: 1, x }))).x).toHaveLength(MAX_COMPARED_FLIGHTS);
  });
});

describe('buildOverlayUrl', () => {
  it('builds <base>?overlay=<id>&s=<snapshot>', () => {
    const url = buildOverlayUrl('http://localhost:5173/', 'graph-speedStarship', {
      v: 1,
      x: [['ift-12.json', '#e91e63']],
    });
    expect(url).toBe(
      'http://localhost:5173/?overlay=graph-speedStarship&s=eyJ2IjoxLCJ4IjpbWyJpZnQtMTIuanNvbiIsIiNlOTFlNjMiXV19',
    );
  });

  it('stays within 2 000 characters for the largest engines snapshot', () => {
    const e = Object.fromEntries(Array.from({ length: 33 }, (_, i) => [String(i + 1), 'RVAC-123']));
    const url = buildOverlayUrl('http://localhost:5173/', 'engines-superheavy', {
      v: 1,
      m: { n: 'Ж'.repeat(MAX_MISSION_NAME), ss: '火'.repeat(MAX_VEHICLE_NAME), sh: '"'.repeat(MAX_VEHICLE_NAME) },
      e,
    });
    expect(url.length).toBeLessThanOrEqual(2000);
  });

  it('stays within 2 000 characters for six compared flights', () => {
    const x: [string, string][] = Array.from({ length: 6 }, (_, i) => [`${'f'.repeat(27)}${i}.json`, '#abcdef']);
    expect(buildOverlayUrl('http://localhost:5173/', 'graph-altitudeStarship', { v: 1, x }).length).toBeLessThanOrEqual(2000);
  });
});

describe('parseOverlayLocation', () => {
  it('returns null for the dashboard', () => {
    expect(parseOverlayLocation('')).toBeNull();
    expect(parseOverlayLocation('?foo=1')).toBeNull();
  });

  it('returns the widget and snapshot', () => {
    const s = encodeSnapshot({ v: 1, h: ['ship33'] });
    expect(parseOverlayLocation(`?overlay=globe&s=${s}`)).toEqual({ widget: 'globe', snapshot: { v: 1, h: ['ship33'] } });
  });

  it('gives defaults when s is missing', () => {
    spyWarn();
    expect(parseOverlayLocation('?overlay=met')).toEqual({ widget: 'met', snapshot: { v: 1 } });
  });

  it('returns an unknown widget id as-is', () => {
    const route = parseOverlayLocation('?overlay=nope');
    expect(route?.widget).toBe('nope');
    expect(isOverlayWidgetId(route!.widget)).toBe(false);
  });
});

describe('OVERLAY_WIDGETS', () => {
  it('has unique ids, all recognised', () => {
    const ids = OVERLAY_WIDGETS.map((w) => w.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.every(isOverlayWidgetId)).toBe(true);
    expect(ids).toContain('graph-accelerationStarship');
  });
});

describe('snapshot properties', () => {
  const hex = fc.array(fc.constantFrom(...'0123456789abcdefABCDEF'), { minLength: 6, maxLength: 6 }).map((d) => `#${d.join('')}`);
  const safeName = (max: number) =>
    fc.array(fc.constantFrom(...'abcXYZ019_.-'), { minLength: 1, maxLength: max }).map((c) => c.join(''));
  // Code points up to max/2 so the UTF-16 length stays within max; control characters removed
  const text = (max: number) =>
    fc.fullUnicodeString({ maxLength: Math.floor(max / 2) }).map((t) => t.replace(/[\u0000-\u001f\u007f]/g, ''));
  const serial = fc
    .array(fc.constantFrom(...'ABCXYZ0123456789-'), { minLength: 1, maxLength: MAX_SERIAL_LENGTH })
    .map((c) => c.join(''));

  const snapshotArb: fc.Arbitrary<OverlaySnapshot> = fc.record(
    {
      v: fc.constant(1 as const),
      x: fc.uniqueArray(fc.tuple(safeName(MAX_FLIGHT_FILENAME), hex), {
        selector: ([f]) => f,
        maxLength: MAX_COMPARED_FLIGHTS,
      }) as fc.Arbitrary<[string, string][]>,
      t: fc.dictionary(safeName(MAX_TRAJECTORY_NAME), hex, { maxKeys: MAX_TRAJECTORIES }),
      h: fc.uniqueArray(safeName(MAX_TRAJECTORY_NAME), { maxLength: MAX_TRAJECTORIES }),
      m: fc.record({ n: text(MAX_MISSION_NAME), ss: text(MAX_VEHICLE_NAME), sh: text(MAX_VEHICLE_NAME) }),
      e: fc.dictionary(fc.integer({ min: 1, max: MAX_ENGINE_NUMBER }).map(String), serial),
    },
    { requiredKeys: ['v'] },
  );

  const widgetArb = fc.constantFrom(...OVERLAY_WIDGETS.map((w) => w.id));

  // Property 11
  it('round-trips any valid snapshot through URL-safe text', () => {
    fc.assert(
      fc.property(snapshotArb, (snapshot) => {
        const encoded = encodeSnapshot(snapshot);
        expect(encoded).toMatch(/^[A-Za-z0-9_-]*$/);
        expect(decodeSnapshot(encoded)).toEqual(snapshot);
      }),
      { numRuns: 300 },
    );
  });

  // Property 13
  it('keeps every encoded URL within 2 000 characters at the caps', () => {
    fc.assert(
      fc.property(widgetArb, snapshotArb, (widget, snapshot) => {
        expect(buildOverlayUrl('http://localhost:5173/', widget, snapshot).length).toBeLessThanOrEqual(2000);
      }),
      { numRuns: 300 },
    );
  });

  it('keeps the URL within 2 000 characters with every field at its cap and 3-byte characters', () => {
    const snapshot: OverlaySnapshot = {
      v: 1,
      x: Array.from({ length: MAX_COMPARED_FLIGHTS }, (_, i) => [`${'f'.repeat(MAX_FLIGHT_FILENAME - 1)}${i}`, '#abcdef']),
      t: Object.fromEntries(Array.from({ length: MAX_TRAJECTORIES }, (_, i) => [`${'t'.repeat(MAX_TRAJECTORY_NAME - 2)}${String(i).padStart(2, '0')}`, '#abcdef'])),
      h: Array.from({ length: MAX_TRAJECTORIES }, (_, i) => `${'h'.repeat(MAX_TRAJECTORY_NAME - 2)}${String(i).padStart(2, '0')}`),
      m: { n: '火'.repeat(MAX_MISSION_NAME), ss: '火'.repeat(MAX_VEHICLE_NAME), sh: '火'.repeat(MAX_VEHICLE_NAME) },
      e: Object.fromEntries(Array.from({ length: MAX_ENGINE_NUMBER }, (_, i) => [String(i + 1), 'RVAC-123'])),
    };
    // Each widget's snapshot holds only its own part
    const full = buildOverlayUrl('http://localhost:5173/', 'engines-superheavy', { v: 1, m: snapshot.m, e: snapshot.e });
    expect(full.length).toBeLessThanOrEqual(2000);
    expect(buildOverlayUrl('http://localhost:5173/', 'globe', { v: 1, t: snapshot.t, h: snapshot.h }).length).toBeLessThanOrEqual(2000);
    expect(buildOverlayUrl('http://localhost:5173/', 'graph-speedStarship', { v: 1, x: snapshot.x }).length).toBeLessThanOrEqual(2000);
  });

  // Property 12
  it('never throws on any string and always returns a valid snapshot', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    fc.assert(
      fc.property(fc.oneof(fc.string(), fc.fullUnicodeString(), fc.stringMatching(/^[A-Za-z0-9_-]*$/), fc.constant(null)), (input) => {
        const result = decodeSnapshot(input);
        expect(result.v).toBe(1);
        // Every field already passes validation: decoding the result again changes nothing
        expect(decodeSnapshot(encodeSnapshot(result))).toEqual(result);
      }),
      { numRuns: 300 },
    );
  });

  it('never throws on well-formed base64url of JSON of any shape, and keeps only valid fields', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const loose = fc.oneof(
      fc.jsonValue(),
      fc.record(
        {
          v: fc.oneof(fc.constant(1), fc.jsonValue()),
          x: fc.jsonValue(),
          t: fc.jsonValue(),
          h: fc.jsonValue(),
          m: fc.jsonValue(),
          e: fc.jsonValue(),
        },
        { requiredKeys: [] },
      ),
    );
    fc.assert(
      fc.property(loose, (value) => {
        const result = decodeSnapshot(encodeText(JSON.stringify(value)));
        expect(result.v).toBe(1);
        expect(decodeSnapshot(encodeSnapshot(result))).toEqual(result);
        for (const [f, c] of result.x ?? []) {
          expect(c).toMatch(/^#[0-9a-f]{6}$/i);
          expect(f.length).toBeLessThanOrEqual(MAX_FLIGHT_FILENAME);
        }
        expect((result.x ?? []).length).toBeLessThanOrEqual(MAX_COMPARED_FLIGHTS);
        expect((result.m?.n ?? '').length).toBeLessThanOrEqual(MAX_MISSION_NAME);
        for (const key of Object.keys(result.e ?? {})) expect(Number(key)).toBeLessThanOrEqual(MAX_ENGINE_NUMBER);
      }),
      { numRuns: 300 },
    );
  });
});

/** base64url of a UTF-8 text, for hand-made payloads. */
function encodeText(text: string): string {
  let binary = '';
  for (const byte of new TextEncoder().encode(text)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

