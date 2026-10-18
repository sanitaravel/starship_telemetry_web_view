import { describe, it, expect } from 'vitest';
import { transformToTimeSeries, type PreviousFlightColumns } from './previous-flights';

/** Build a columnar flight whose Super Heavy and Starship share the same speeds. */
function columns(rows: [seconds: number, realTimeSeconds: number, speed: number | null][]): PreviousFlightColumns {
  const speeds = rows.map(([, , speed]) => speed);
  return {
    met: rows.map(([seconds]) => `+00:00:${String(seconds).padStart(2, '0')}`),
    t: rows.map(([, realTimeSeconds]) => realTimeSeconds),
    ss_speed: speeds,
    sh_speed: speeds,
    ss_alt: rows.map(() => null),
    sh_alt: rows.map(() => null),
  };
}

describe('transformToTimeSeries', () => {
  it('gives each sample a sub-second MET anchored to the recorded clock', () => {
    // real_time_seconds is 2 s behind the MET clock (frame-clock drift)
    const store = transformToTimeSeries(columns([
      [10, 8.0, 100],
      [10, 8.333, 110],
      [10, 8.667, 120],
      [11, 9.0, 130],
    ]));

    const xs = store.speedSuperHeavy.map((p) => p.metSeconds);
    expect(xs[0]).toBe(10);
    expect(xs[1]).toBeCloseTo(10.333, 10);
    expect(xs[2]).toBeCloseTo(10.667, 10);
    expect(xs[3]).toBeCloseTo(11, 10);
    expect(store.speedSuperHeavy[3].missionElapsedTime).toBe('+00:00:11');
  });
});

describe('transformToTimeSeries null handling', () => {
  it('skips null readings per series and keeps the rest', () => {
    const store = transformToTimeSeries({
      met: ['+00:00:01', '+00:00:02'],
      t: [1, 2],
      ss_speed: [null, 5],
      sh_speed: [3, null],
      ss_alt: [0.1, 0.2],
      sh_alt: [null, null],
    });
    expect(store.speedStarship.map((p) => p.value)).toEqual([5]);
    expect(store.speedSuperHeavy.map((p) => p.value)).toEqual([3]);
    expect(store.altitudeStarship.map((p) => p.unit)).toEqual(['km', 'km']);
    expect(store.altitudeSuperHeavy).toEqual([]);
  });
});
