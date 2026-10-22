import { describe, it, expect } from 'vitest';
import type { PreviousFlightColumns } from '../types';
import { detectFlightEngineEvents, estimateSeparationMet, transformToTimeSeries } from './flights';

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

describe('detectFlightEngineEvents', () => {
  const allSh = (status: 'active' | 'inactive', except: Record<string, 'active' | 'inactive'> = {}) =>
    Object.fromEntries(Array.from({ length: 33 }, (_, i) => [`sh_e${i + 1}`, except[`sh_e${i + 1}`] ?? status]));

  /** 5 rows per second from MET 50 to 70 with the given engine changes. */
  function flight(engines: PreviousFlightColumns['engines']): PreviousFlightColumns {
    const rows = Array.from({ length: 100 }, (_, k) => 50 + k / 5);
    return {
      met: rows.map((t) => `+00:00:${String(Math.floor(t)).padStart(2, '0')}`),
      t: rows,
      ss_speed: rows.map(() => null),
      sh_speed: rows.map(() => null),
      ss_alt: rows.map(() => null),
      sh_alt: rows.map(() => null),
      engines,
    };
  }

  it('has no events without engine statuses', () => {
    expect(detectFlightEngineEvents(flight(undefined))).toEqual([]);
    expect(detectFlightEngineEvents(flight([]))).toEqual([]);
  });

  it('replays status changes: each map holds until the next change', () => {
    // Row 50 is MET 60
    const events = detectFlightEngineEvents(flight([
      { i: 0, sh: allSh('active'), ss: { ss_e1: 'inactive' } },
      { i: 50, sh: allSh('active', { sh_e20: 'inactive' }) },
    ]));
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ kind: 'engine_out', vehicle: 'superheavy', engineIds: ['sh_e20'], runningAfter: 32 });
    expect(events[0].metSeconds).toBeCloseTo(60, 5);
  });
});

describe('estimateSeparationMet', () => {
  it('is the MET of the row after the last shared reading', () => {
    const flight = columns([[40, 40, 100], [41, 41, 200], [41, 41.5, 300], [42, 42, 400], [43, 43, 500]]);
    flight.ss_speed = [100, 200, 300, 410, 500];
    // Super Heavy unread right after separation, then differing
    flight.sh_speed = [100, 200, 300, null, 390];
    expect(estimateSeparationMet(flight, [40, 41, 41.5, 42, 43])).toBe(42);
  });

  it('ignores shared and differing readings up to T+30', () => {
    const flight = columns([[0, 0, 70], [10, 10, 100], [31, 31, 300], [32, 32, 400]]);
    flight.ss_speed = [70, 100, 300, 410];
    flight.sh_speed = [30, 100, 300, 390];
    expect(estimateSeparationMet(flight, [0, 10, 31, 32])).toBe(32);
  });

  it('is one row spacing after the last row when the reading is shared to the end', () => {
    const flight = columns([[40, 40, 100], [42, 42, 200]]);
    expect(estimateSeparationMet(flight, [40, 42])).toBe(44);
  });

  it('is null without a shared reading after T+30', () => {
    expect(estimateSeparationMet(columns([[10, 10, 100], [20, 20, 300]]), [10, 20])).toBeNull();
    const flight = columns([[40, 40, 100], [41, 41, 200]]);
    flight.sh_speed = [null, 190];
    expect(estimateSeparationMet(flight, [40, 41])).toBeNull();
  });
});
