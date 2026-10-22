import { describe, it, expect } from 'vitest';
import type { TimeSeriesPoint } from './time-series';
import { pointSeconds } from './met';

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
