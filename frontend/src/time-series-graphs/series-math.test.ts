import { describe, it, expect } from 'vitest';
import type { TimeSeriesPoint } from '../state';
import { computeAcceleration, ACCELERATION_MAX_DT_SECONDS, STANDARD_GRAVITY } from './series-math';

function pt(met: string, value: number): TimeSeriesPoint {
  return { missionElapsedTime: met, timestamp: 0, value, unit: 'km/h' };
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
});
