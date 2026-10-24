import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { TimeSeriesPoint } from './time-series';
import { parseMETToSeconds, pointSeconds } from './met';

function pt(met: string, value: number, metSeconds?: number): TimeSeriesPoint {
  return { missionElapsedTime: met, metSeconds, timestamp: 0, value, unit: 'km/h' };
}

describe('pointSeconds', () => {
  it('prefers the sub-second estimate', () => {
    expect(pointSeconds(pt('+00:00:05', 0, 5.667))).toBe(5.667);
  });

  it('falls back to the whole-second MET string', () => {
    expect(pointSeconds(pt('T+00:01:05', 0))).toBe(65);
  });
});

describe('parseMETToSeconds', () => {
  it.each([
    ['00:01:23', 83],
    ['+00:01:23', 83],
    ['T+00:01:23', 83],
    ['-00:00:05', -5],
    ['T-00:12:31', -751],
    ['T- 00:00:27', -27],
    ['T−00:00:27', -27],
    ['01:02:03', 3723],
  ])('%s -> %d', (met, seconds) => {
    expect(parseMETToSeconds(met)).toBe(seconds);
  });

  it('reads garbage as 0, as before', () => {
    expect(parseMETToSeconds('')).toBe(0);
    expect(parseMETToSeconds('abc')).toBe(0);
  });

  /** The parser before signed "T-" was understood, for the inputs it handled. */
  function previous(met: string): number {
    const sign = met.startsWith('-') ? -1 : 1;
    const parts = met.replace(/^[+\-T]/, '').split(':');
    return sign * ((parseInt(parts[0], 10) || 0) * 3600 + (parseInt(parts[1], 10) || 0) * 60 + (parseInt(parts[2], 10) || 0));
  }

  const hms = fc
    .tuple(fc.integer({ min: 0, max: 23 }), fc.integer({ min: 0, max: 59 }), fc.integer({ min: 0, max: 59 }))
    .map(([h, m, s]) => [h, m, s].map((n) => String(n).padStart(2, '0')).join(':'));

  it('reads positive and "-" METs as before', () => {
    fc.assert(
      fc.property(hms, fc.constantFrom('', '+', 'T+', '-'), (time, prefix) => {
        expect(parseMETToSeconds(prefix + time)).toBe(previous(prefix + time));
      }),
    );
  });

  it('"T-" is the negative of "T+"', () => {
    fc.assert(
      fc.property(hms, (time) => {
        expect(parseMETToSeconds(`T-${time}`) + parseMETToSeconds(`T+${time}`)).toBe(0);
      }),
    );
  });
});
