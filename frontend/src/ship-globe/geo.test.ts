import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  applyDrag,
  formatLatitude,
  formatLongitude,
  gpsToUnixMs,
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

  it('converts GPS seconds to UTC', () => {
    // GPS week 2362, 2025-04-13 00:00:18 GPS = 00:00:00 UTC
    const gps = 2362 * 7 * 86400 + 18;
    expect(new Date(gpsToUnixMs(gps)).toISOString()).toBe('2025-04-13T00:00:00.000Z');
  });
});
