import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  buildOverlayUrl,
  decodeSnapshot,
  encodeSnapshot,
  MAX_COMPARED_FLIGHTS,
  MAX_MISSION_NAME,
  MAX_VEHICLE_NAME,
  parseOverlayLocation,
  type OverlaySnapshot,
} from './snapshot';
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

/** base64url of a UTF-8 text, for hand-made payloads. */
function encodeText(text: string): string {
  let binary = '';
  for (const byte of new TextEncoder().encode(text)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

