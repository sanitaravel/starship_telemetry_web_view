/**
 * Saved-flight engine path: change-list columns replayed through the
 * detector, and the separation estimate that picks events for the charts.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { EngineStatusChange, PreviousFlightColumns } from '../core/types';
import { enginesInRing, enginesOf } from './engines/layout';
import { EngineEventDetector } from './engines/detector';
import type { EngineStatusMap } from './engines/events';
import { detectFlightEngineEvents, estimateSeparationMet } from './flights';
import { eventsForSeries } from '../time-series-graphs/engine-markers';

const ids = (engines: readonly { id: string }[]) => engines.map((e) => e.id);
const SH_ALL = ids(enginesOf('superheavy'));
const SS_ALL = ids(enginesOf('starship'));
const INNER = ids(enginesInRing('superheavy', 'engines_inner'));
const MIDDLE = ids(enginesInRing('superheavy', 'engines_middle'));
const without = (all: readonly string[], ...out: string[]) => all.filter((id) => !out.includes(id));

type Panel = 'hidden' | readonly string[];

function statuses(all: readonly string[], panel: Panel): EngineStatusMap {
  return Object.fromEntries(
    all.map((id) => [id, panel === 'hidden' ? 'undetected' : panel.includes(id) ? 'active' : 'inactive']),
  );
}

function metString(t: number): string {
  const s = Math.floor(Math.abs(t));
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${t < 0 ? '-' : '+'}${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
}

interface Row { t: number; ss: EngineStatusMap; sh: EngineStatusMap; ssSpeed?: number | null; shSpeed?: number | null }

/** Columns the way the backend's to_columns builds them: a map only where it changed. */
function toColumns(rows: Row[]): PreviousFlightColumns {
  const engines: EngineStatusChange[] = [];
  let lastSs: string | undefined;
  let lastSh: string | undefined;
  rows.forEach((row, i) => {
    const change: EngineStatusChange = { i };
    const ss = JSON.stringify(row.ss);
    const sh = JSON.stringify(row.sh);
    if (Object.keys(row.ss).length > 0 && ss !== lastSs) { change.ss = row.ss; lastSs = ss; }
    if (Object.keys(row.sh).length > 0 && sh !== lastSh) { change.sh = row.sh; lastSh = sh; }
    if (change.ss || change.sh) engines.push(change);
  });
  return {
    met: rows.map((r) => metString(r.t)),
    t: rows.map((r) => r.t),
    ss_speed: rows.map((r) => r.ssSpeed ?? null),
    sh_speed: rows.map((r) => r.shSpeed ?? null),
    ss_alt: rows.map(() => null),
    sh_alt: rows.map(() => null),
    engines,
  };
}

/** The live path: every row pushed with its full maps. */
function detectRows(rows: Row[]) {
  const detector = new EngineEventDetector();
  for (const row of rows) detector.pushStatuses(row.t, row.ss, row.sh);
  detector.flush();
  return detector.events();
}

function rowsEvery(from: number, to: number, dt: number, frame: (t: number) => { ss: Panel; sh: Panel }): Row[] {
  const rows: Row[] = [];
  for (let k = 0; from + k * dt < to - 1e-9; k++) {
    const t = Math.round((from + k * dt) * 1000) / 1000;
    const f = frame(t);
    rows.push({ t, ss: statuses(SS_ALL, f.ss), sh: statuses(SH_ALL, f.sh) });
  }
  return rows;
}

describe('detectFlightEngineEvents replays the change list like the live path', () => {
  it('IFT-13-like saved flight at 1 row/s: landing failed_to_light and 10 -> 8 engine_out', () => {
    const lit = [...INNER, ...MIDDLE.slice(0, 7)];
    const sh = (t: number): Panel => {
      if (t >= 430) return 'hidden';
      if (t < 142) return SH_ALL;
      if (t < 150) return [...INNER, 'sh_e4', 'sh_e9'];
      if (t < 200) return SH_ALL;
      if (t < 400) return [];
      if (t < 410) return lit;
      if (t < 415) return without(lit, 'sh_e9', 'sh_e10');
      if (t < 425) return [...INNER, 'sh_e4', 'sh_e5'];
      return [];
    };
    const ss = (t: number): Panel => (t >= 144 && t < 520 ? SS_ALL : []);
    const rows = rowsEvery(-5, 600, 1, (t) => ({ sh: sh(t), ss: ss(t) }));
    const columns = toColumns(rows);
    const events = detectFlightEngineEvents(columns, rows.map((r) => r.t));
    expect(events.map((e) => [e.kind, e.vehicle, e.engineIds, e.metSeconds, e.runningAfter, e.expected])).toEqual([
      ['failed_to_light', 'superheavy', ['sh_e11', 'sh_e12', 'sh_e13'], 400, 10, 13],
      ['engine_out', 'superheavy', ['sh_e9', 'sh_e10'], 410, 8, 10],
    ]);
    expect(events).toEqual(detectRows(rows));
    // The change list is compact: far fewer entries than rows
    expect(columns.engines!.length).toBeLessThan(20);
  });

  it('a shutdown inside an all-undetected gap keeps its metRange through the change list', () => {
    const rows = rowsEvery(30, 120, 1, (t) => ({
      ss: [],
      sh: t >= 60 && t < 70 ? 'hidden' : t < 60 ? SH_ALL : without(SH_ALL, 'sh_e20'),
    }));
    const events = detectFlightEngineEvents(toColumns(rows), rows.map((r) => r.t));
    expect(events).toEqual([
      expect.objectContaining({ kind: 'engine_out', engineIds: ['sh_e20'], metSeconds: 70, metRange: [59, 70] }),
    ]);
  });

  it('property: same events as pushing every row (random 5 fps flights)', () => {
    const panelArb = (all: readonly string[]): fc.Arbitrary<Panel> =>
      fc.oneof(fc.constant<Panel>('hidden'), fc.subarray([...all]));
    const flightArb = fc
      .tuple(
        fc.array(fc.record({ frames: fc.integer({ min: 1, max: 30 }), sh: panelArb(SH_ALL), ss: panelArb(SS_ALL) }), { maxLength: 20 }),
        fc.integer({ min: -20, max: 4000 }),
      )
      .map(([runs, start]) => {
        const rows: Row[] = [];
        let t = start;
        for (const run of runs) {
          for (let k = 0; k < run.frames; k++) {
            rows.push({ t, ss: statuses(SS_ALL, run.ss), sh: statuses(SH_ALL, run.sh) });
            t = Math.round((t + 0.2) * 1000) / 1000;
          }
        }
        return rows;
      });
    fc.assert(
      fc.property(flightArb, (rows) => {
        expect(detectFlightEngineEvents(toColumns(rows), rows.map((r) => r.t))).toEqual(detectRows(rows));
      }),
      { numRuns: 100 },
    );
  });
});

/**
 * Speeds shaped like results_previous/ift-12.json and ift-13.json: before
 * separation both vehicles share one reading; for ~60 s after it the Super
 * Heavy speed is unread (null), so the first row where both speeds exist
 * and differ is about a minute after the real separation.
 */
function separationFlight(): { columns: PreviousFlightColumns; metSeconds: number[] } {
  const rows: Row[] = [];
  for (let t = 100; t < 260; t++) {
    let ssSpeed: number | null = 4000 + t * 10;
    let shSpeed: number | null = ssSpeed;
    if (t >= 160) shSpeed = t < 221 ? null : 4000 - t; // separation at T+160
    rows.push({ t, ss: {}, sh: {}, ssSpeed, shSpeed });
  }
  return { columns: toColumns(rows), metSeconds: rows.map((r) => r.t) };
}

describe('estimateSeparationMet on saved-flight shaped speeds', () => {
  it('is no later than the first row after the shared reading ends', () => {
    const { columns, metSeconds } = separationFlight();
    const estimate = estimateSeparationMet(columns, metSeconds);
    expect(estimate).not.toBeNull();
    expect(estimate!).toBeLessThanOrEqual(160);
  });

  it('keeps a Super Heavy boostback event (after separation) off the Starship charts', () => {
    const { columns, metSeconds } = separationFlight();
    const boostback = {
      kind: 'died_after_relight' as const,
      vehicle: 'superheavy' as const,
      engineIds: ['sh_e5'],
      rings: ['engines_middle'],
      metSeconds: 175,
      runningAfter: 12,
      expected: 13,
    };
    expect(eventsForSeries([boostback], 'starship', estimateSeparationMet(columns, metSeconds))).toEqual([]);
  });

  it('is not fooled by differing pad readings at T+0 (ift-14.json reads 70 vs 30 km/h at T+00:00:00)', () => {
    const rows: Row[] = [];
    for (let t = 0; t < 260; t++) {
      let ssSpeed: number | null = t < 3 ? 70 : 4000 + t * 10;
      let shSpeed: number | null = t < 3 ? 30 : ssSpeed;
      if (t >= 160) shSpeed = 4000 - t;
      rows.push({ t, ss: {}, sh: {}, ssSpeed, shSpeed });
    }
    const estimate = estimateSeparationMet(toColumns(rows), rows.map((r) => r.t));
    // An ascent Super Heavy event at e.g. T+102 must still be dated before separation
    expect(estimate).not.toBeNull();
    expect(estimate!).toBeGreaterThan(102);
    expect(estimate!).toBeLessThanOrEqual(160);
  });
});
