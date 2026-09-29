import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  applyDrag,
  formatLatitude,
  formatLongitude,
  gpsToUnixMs,
  gradientSegments,
  hexToRgba,
  isOnVisibleHemisphere,
  normalizeLongitude,
  rotationToCenter,
} from './geo';

describe('geo helpers', () => {
  it('centres the rotation on the given point', () => {
    const rotation = rotationToCenter(106.4, -17.3);
    expect(isOnVisibleHemisphere(106.4, -17.3, rotation)).toBe(true);
    expect(isOnVisibleHemisphere(-73.6, 17.3, rotation)).toBe(false); // antipode
  });

  it('keeps drag rotation within valid ranges (property)', () => {
    fc.assert(
      fc.property(
        fc.double({ min: -180, max: 180, noNaN: true }),
        fc.double({ min: -90, max: 90, noNaN: true }),
        fc.double({ min: -5000, max: 5000, noNaN: true }),
        fc.double({ min: -5000, max: 5000, noNaN: true }),
        (lambda, phi, dx, dy) => {
          const [l, p] = applyDrag([lambda, phi], dx, dy, 200);
          expect(l).toBeGreaterThanOrEqual(-180);
          expect(l).toBeLessThan(180);
          expect(p).toBeGreaterThanOrEqual(-90);
          expect(p).toBeLessThanOrEqual(90);
        },
      ),
    );
  });

  it('dragging right turns the globe eastward', () => {
    const [lambda] = applyDrag([0, 0], 10, 0, 100);
    expect(lambda).toBeGreaterThan(0);
  });

  it('normalizes longitude into [-180, 180)', () => {
    expect(normalizeLongitude(190)).toBe(-170);
    expect(normalizeLongitude(-190)).toBe(170);
    expect(normalizeLongitude(180)).toBe(-180);
  });

  it('formats coordinates with hemisphere suffixes', () => {
    expect(formatLatitude(-17.3881)).toBe('17.388° S');
    expect(formatLongitude(106.4566)).toBe('106.457° E');
    expect(formatLongitude(-97.157)).toBe('97.157° W');
  });

  it('splits a path into gradient slices from start (t=0) to end (t=1)', () => {
    const coords: [number, number][] = [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]];
    expect(gradientSegments(coords, 2)).toEqual([
      { coordinates: [[0, 0], [1, 0], [2, 0]], t: 0 },
      { coordinates: [[2, 0], [3, 0], [4, 0]], t: 1 },
    ]);
  });

  it('returns no slices for paths shorter than two points', () => {
    expect(gradientSegments([], 10)).toEqual([]);
    expect(gradientSegments([[1, 1]], 10)).toEqual([]);
  });

  it('gradient slices cover the whole path without gaps (property)', () => {
    fc.assert(
      fc.property(
        fc.array(fc.tuple(fc.integer(), fc.integer()), { minLength: 2, maxLength: 400 }),
        fc.integer({ min: 1, max: 64 }),
        (coords, count) => {
          const slices = gradientSegments(coords as [number, number][], count);
          expect(slices.length).toBe(Math.min(count, coords.length - 1));
          expect(slices[0].coordinates[0]).toBe(coords[0]);
          expect(slices[slices.length - 1].coordinates.at(-1)).toBe(coords[coords.length - 1]);
          for (let i = 1; i < slices.length; i++) {
            expect(slices[i].coordinates[0]).toBe(slices[i - 1].coordinates.at(-1));
            expect(slices[i].t).toBeGreaterThan(slices[i - 1].t);
          }
        },
      ),
    );
  });

  it('converts hex colours to rgba', () => {
    expect(hexToRgba('#4fc3f7', 0.5)).toBe('rgba(79, 195, 247, 0.5)');
  });

  it('converts GPS seconds to UTC', () => {
    // GPS week 2362, 2025-04-13 00:00:18 GPS = 00:00:00 UTC
    const gps = 2362 * 7 * 86400 + 18;
    expect(new Date(gpsToUnixMs(gps)).toISOString()).toBe('2025-04-13T00:00:00.000Z');
  });
});
