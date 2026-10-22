import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { TimeSeriesPoint } from '../core/time-series';
import { pointSeconds } from '../core/met';
import {
  computeAcceleration,
  deduplicateByX,
  IncrementalXY,
  ACCELERATION_MAX_DT_SECONDS,
  STANDARD_GRAVITY,
} from './series-math';

function pt(met: string, value: number, metSeconds?: number): TimeSeriesPoint {
  return { missionElapsedTime: met, metSeconds, timestamp: 0, value, unit: 'km/h' };
}

describe('computeAcceleration', () => {
  it('returns an empty series for fewer than two points', () => {
    expect(computeAcceleration([])).toEqual([]);
    expect(computeAcceleration([pt('+00:00:01', 100)])).toEqual([]);
  });

  it('converts km/h per second into g at the interval midpoint', () => {
    // 36 km/h = 10 m/s gained over 1 s
    const result = computeAcceleration([pt('+00:00:00', 0), pt('+00:00:01', 36)]);
    expect(result).toHaveLength(1);
    expect(result[0].x).toBe(0.5);
    expect(result[0].y).toBeCloseTo(10 / STANDARD_GRAVITY, 10);
  });

  it('reports deceleration as negative g', () => {
    const result = computeAcceleration([pt('+00:00:10', 72), pt('+00:00:12', 0)]);
    expect(result[0].y).toBeCloseTo(-10 / STANDARD_GRAVITY, 10);
  });

  it('sorts unordered input and keeps the first value for duplicate times', () => {
    const result = computeAcceleration([
      pt('+00:00:02', 72),
      pt('+00:00:00', 0),
      pt('+00:00:02', 999),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0].x).toBe(1);
    expect(result[0].y).toBeCloseTo(10 / STANDARD_GRAVITY, 10);
  });

  it('emits a null gap marker when samples are too far apart', () => {
    const gap = ACCELERATION_MAX_DT_SECONDS + 1;
    const result = computeAcceleration([
      pt('+00:00:00', 0),
      pt(`+00:00:${String(gap).padStart(2, '0')}`, 36),
    ]);
    expect(result).toEqual([{ x: gap / 2, y: null }]);
  });

  it('differences sub-second samples over at least one second', () => {
    // Steady 1 g (35.30394 km/h per second), sampled 3 times per second
    const kmhPerS = STANDARD_GRAVITY * 3.6;
    const points = Array.from({ length: 10 }, (_, k) =>
      pt('+00:00:00', kmhPerS * (k / 3), k / 3),
    );
    const result = computeAcceleration(points);
    // Samples 0-2 have no sample a full second earlier
    expect(result).toHaveLength(7);
    for (const p of result) {
      expect(p.y).toBeCloseTo(1, 10);
    }
    // First output: sample at 1 s against sample at 0 s
    expect(result[0].x).toBeCloseTo(0.5, 10);
  });

  it('ignores a short-interval speed glitch that would spike a neighbour difference', () => {
    const points = [
      pt('+00:00:00', 0, 0),
      pt('+00:00:00', 10, 0.333),
      pt('+00:00:00', 20, 0.667),
      pt('+00:00:01', 30, 1),
      pt('+00:00:01', 36, 1.333),
    ];
    const result = computeAcceleration(points);
    // 1.333 is compared with 0.333 (1 s back), not with its 0.333 s neighbour
    expect(result).toHaveLength(2);
    expect(result[1].x).toBeCloseTo(0.833, 3);
    expect(result[1].y).toBeCloseTo(((26 / 3.6) / 1) / STANDARD_GRAVITY, 3);
  });
});

describe('IncrementalXY', () => {
  const full = (points: TimeSeriesPoint[]) =>
    deduplicateByX(points.map((p) => ({ x: pointSeconds(p), y: p.value })));

  it('matches a full deduplicateByX after every append, in or out of order', () => {
    // Mostly increasing times with occasional repeats and steps back
    const step = fc.record({
      dt: fc.oneof(fc.constant(0), fc.integer({ min: 1, max: 8 }), fc.integer({ min: -3, max: -1 })),
      value: fc.integer({ min: 0, max: 30000 }),
    });
    fc.assert(
      fc.property(fc.array(step, { maxLength: 60 }), (steps) => {
        const series = new IncrementalXY();
        const points: TimeSeriesPoint[] = [];
        let t = 0;
        for (const { dt, value } of steps) {
          t += dt;
          points.push(pt('+00:00:00', value, t));
          expect(series.update(points)).toEqual(full(points));
        }
      }),
    );
  });

  it('extends the same array while points arrive in order', () => {
    const series = new IncrementalXY();
    const points = [pt('+00:00:01', 1, 1)];
    const first = series.update(points);
    points.push(pt('+00:00:02', 2, 2));
    expect(series.update(points)).toBe(first);
    expect(first).toEqual([{ x: 1, y: 1 }, { x: 2, y: 2 }]);
  });

  it('rebuilds when handed a different source array', () => {
    const series = new IncrementalXY();
    series.update([pt('+00:00:01', 1, 1), pt('+00:00:02', 2, 2)]);
    expect(series.update([pt('+00:00:05', 5, 5)])).toEqual([{ x: 5, y: 5 }]);
  });
});
