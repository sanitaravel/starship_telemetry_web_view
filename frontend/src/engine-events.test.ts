import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { TelemetryRecord } from './types';
import { enginesInRing, enginesOf } from './engine-layout';
import {
  EngineEventDetector,
  detectEngineEvents,
  isShipLandingBurn,
  shipAltitudeKm,
  type EngineEvent,
  type EngineStatusMap,
} from './engine-events';

const ids = (engines: readonly { id: string }[]) => engines.map((e) => e.id);
const SH_ALL = ids(enginesOf('superheavy'));
const SS_ALL = ids(enginesOf('starship'));
const INNER = ids(enginesInRing('superheavy', 'engines_inner'));
const MIDDLE = ids(enginesInRing('superheavy', 'engines_middle'));
const OUTER = ids(enginesInRing('superheavy', 'engines_outer'));
const SEA_LEVEL = ids(enginesInRing('starship', 'atmo'));
const without = (all: readonly string[], ...out: string[]) => all.filter((id) => !out.includes(id));

/** A vehicle panel in one frame: hidden, or the engines that are on (the rest read off). */
type Panel = 'hidden' | { on: readonly string[]; undetected?: readonly string[] };

interface Frame {
  sh?: Panel;
  ss?: Panel;
  /** Ship altitude in km (post-separation reading on the left, Starship side). */
  shipAlt?: number;
}

function statuses(all: readonly string[], panel: Panel | undefined): EngineStatusMap {
  const map: EngineStatusMap = {};
  for (const id of all) {
    if (!panel || panel === 'hidden' || panel.undetected?.includes(id)) map[id] = 'undetected';
    else map[id] = panel.on.includes(id) ? 'active' : 'inactive';
  }
  return map;
}

function met(t: number): string {
  const s = Math.floor(Math.abs(t));
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${t < 0 ? '-' : '+'}${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
}

let sequence = 0;

function record(t: number, frame: Frame): TelemetryRecord {
  const reading = (value: number | null) => ({
    value,
    unit: value === null ? null : 'KM',
    status: value === null ? 'not_found' : 'available',
  });
  return {
    sequence_number: sequence++,
    mission_elapsed_time: met(t),
    mission_elapsed_time_raw: null,
    speed_left: reading(null),
    speed_right: reading(null),
    altitude_left: reading(frame.shipAlt ?? null),
    altitude_right: reading(null),
    stage_left_label: null,
    stage_right_label: null,
    stage_separation_text: null,
    stage_assignment_left: 'starship',
    stage_assignment_right: 'super_heavy',
    separation_state: 'post_separation',
    t_zero_found: true,
    stage_sep_found: true,
    starship_engines: statuses(SS_ALL, frame.ss),
    superheavy_engines: statuses(SH_ALL, frame.sh),
    detection_accuracy: { starship: 1, superheavy: 1 },
    timestamp: Math.round(t * 1000),
  };
}

const DT = 0.2;

/** Frames every DT seconds from `from` (inclusive) to `to` (exclusive). */
function timeline(from: number, to: number, frame: (t: number) => Frame): TelemetryRecord[] {
  const records: TelemetryRecord[] = [];
  const n = Math.round((to - from) / DT);
  for (let k = 0; k < n; k++) {
    const t = Math.round((from + k * DT) * 1000) / 1000;
    records.push(record(t, frame(t)));
  }
  return records;
}

const exactMet = (r: TelemetryRecord) => r.timestamp / 1000;
const detect = (records: TelemetryRecord[]) => detectEngineEvents(records, exactMet);

/** On-engine list that changes at the given times: [[t, on], ...], first entry from the start. */
function schedule(...steps: [number, readonly string[]][]): (t: number) => readonly string[] {
  return (t) => {
    let on = steps[0][1];
    for (const [at, list] of steps) if (t >= at - 1e-9) on = list;
    return on;
  };
}

describe('EngineEventDetector: no events', () => {
  it('ignores records where every engine is undetected', () => {
    expect(detect(timeline(0, 60, () => ({})))).toEqual([]);
  });

  it('ignores a single-frame flicker and a sub-0.5 s flicker', () => {
    const records = timeline(0, 30, (t) => {
      const flicker = Math.abs(t - 10) < 1e-6 || (t > 19.9 && t < 20.3);
      return { sh: { on: flicker ? without(SH_ALL, 'sh_e20') : SH_ALL } };
    });
    expect(detect(records)).toEqual([]);
  });

  it('does not treat a whole-vehicle gap as a shutdown', () => {
    const records = timeline(0, 30, (t) => ({ sh: t >= 10 && t < 20 ? 'hidden' : { on: SH_ALL } }));
    expect(detect(records)).toEqual([]);
  });

  it('keeps the previous state of a single undetected engine on a visible panel', () => {
    const records = timeline(0, 30, (t) => ({
      sh: { on: SH_ALL, undetected: t >= 10 && t < 20 ? ['sh_e20'] : [] },
    }));
    expect(detect(records)).toEqual([]);
  });

  it('V2 MECO 33 -> 3 staggered over 2 s', () => {
    // Outer ring shuts down over the first second, middle ring over the next
    const on = (t: number) => {
      if (t < 160) return SH_ALL;
      const outerOff = OUTER.slice(0, Math.min(20, Math.floor((t - 160) * 20) + 1));
      const middleOff = t < 161 ? [] : MIDDLE.slice(0, Math.min(10, Math.floor((t - 161) * 10) + 1));
      return without(SH_ALL, ...outerOff, ...middleOff);
    };
    expect(detect(timeline(100, 175, (t) => ({ sh: { on: on(t) } })))).toEqual([]);
  });

  it('V3 MECO 33 -> 5 staggered over 2 s', () => {
    const keep = [...INNER, 'sh_e4', 'sh_e9'];
    const off = without(SH_ALL, ...keep);
    const on = (t: number) =>
      t < 160 ? SH_ALL : without(SH_ALL, ...off.slice(0, Math.min(off.length, Math.floor((t - 160) * 14) + 1)));
    expect(detect(timeline(100, 175, (t) => ({ sh: { on: on(t) } })))).toEqual([]);
  });

  it('ship SECO 6 -> 0', () => {
    const on = schedule([0, []], [160, SS_ALL], [500, []]);
    expect(detect(timeline(100, 520, (t) => ({ ss: { on: on(t) } })))).toEqual([]);
  });

  it('ship landing burn 3 -> 2 -> 1 at MET 3900', () => {
    const on = schedule([0, []], [3900, SEA_LEVEL], [3910, ['ss_e1', 'ss_e2']], [3915, ['ss_e1']], [3920, []]);
    expect(detect(timeline(3880, 3930, (t) => ({ ss: { on: on(t) } })))).toEqual([]);
  });

  it('Super Heavy landing burn 13 -> 5 -> 3', () => {
    const on = schedule(
      [0, []],
      [400, [...INNER, ...MIDDLE]],
      [410, [...INNER, 'sh_e4', 'sh_e9']],
      [415, INNER],
      [425, []],
    );
    expect(detect(timeline(390, 430, (t) => ({ sh: { on: on(t) } })))).toEqual([]);
  });
});

describe('EngineEventDetector: events', () => {
  it('Super Heavy ascent 33 -> 32 is an outer-ring engine_out', () => {
    const on = schedule([0, SH_ALL], [60, without(SH_ALL, 'sh_e20')]);
    expect(detect(timeline(30, 80, (t) => ({ sh: { on: on(t) } })))).toEqual([
      {
        kind: 'engine_out',
        vehicle: 'superheavy',
        engineIds: ['sh_e20'],
        rings: ['engines_outer'],
        metSeconds: 60,
        runningAfter: 32,
        expected: 33,
      },
    ]);
  });

  it('ship 6 -> 5 on a vacuum engine is engine_out 5/6', () => {
    const on = schedule([0, []], [160, SS_ALL], [300, without(SS_ALL, 'ss_e5')]);
    expect(detect(timeline(100, 320, (t) => ({ ss: { on: on(t) } })))).toEqual([
      {
        kind: 'engine_out',
        vehicle: 'starship',
        engineIds: ['ss_e5'],
        rings: ['vacuum'],
        metSeconds: 300,
        runningAfter: 5,
        expected: 6,
      },
    ]);
  });

  it('ship landing with 2 of 3 sea-level engines lit is failed_to_light', () => {
    const on = schedule([0, []], [3900, ['ss_e1', 'ss_e2']], [3915, ['ss_e1']], [3920, []]);
    expect(detect(timeline(3880, 3930, (t) => ({ ss: { on: on(t) } })))).toEqual([
      {
        kind: 'failed_to_light',
        vehicle: 'starship',
        engineIds: ['ss_e3'],
        rings: ['atmo'],
        metSeconds: 3900,
        runningAfter: 2,
        expected: 3,
      },
    ]);
  });

  it('does not check the liftoff burn for failed_to_light', () => {
    const on = schedule([0, []], [-3, without(SH_ALL, 'sh_e20')]);
    expect(detect(timeline(-10, 20, (t) => ({ sh: { on: on(t) } })))).toEqual([]);
  });

  it('checks the ship ascent burn for failed_to_light', () => {
    const on = schedule([0, []], [160, without(SS_ALL, 'ss_e6')], [500, []]);
    expect(detect(timeline(100, 520, (t) => ({ ss: { on: on(t) } })))).toEqual([
      {
        kind: 'failed_to_light',
        vehicle: 'starship',
        engineIds: ['ss_e6'],
        rings: ['vacuum'],
        metSeconds: 160,
        runningAfter: 5,
        expected: 6,
      },
    ]);
  });

  it('ship landing burn is recognised by altitude below 30 km', () => {
    // Early MET, but low: 2 -> 1 is a planned throttle-down
    const on = schedule([0, []], [600, SEA_LEVEL], [610, ['ss_e1', 'ss_e2']], [615, ['ss_e1']], [620, []]);
    expect(detect(timeline(580, 630, (t) => ({ ss: { on: on(t) }, shipAlt: 2 })))).toEqual([]);
    // Same at altitude: 3 -> 2 is an engine out
    const high = detect(timeline(580, 630, (t) => ({ ss: { on: on(t) }, shipAlt: 100 })));
    expect(high.map((e) => [e.kind, e.engineIds])).toEqual([['engine_out', ['ss_e3']], ['engine_out', ['ss_e2']]]);
  });

  it('Super Heavy landing with 10 of 13 lit: failed_to_light, then 10 -> 8 engine_out, then 8 -> 5 planned', () => {
    const lit = [...INNER, ...MIDDLE.slice(0, 7)]; // e1-e10
    const on = schedule(
      [0, []],
      [400, lit],
      [410, without(lit, 'sh_e9', 'sh_e10')],
      [415, [...INNER, 'sh_e4', 'sh_e5']],
      [425, []],
    );
    expect(detect(timeline(390, 430, (t) => ({ sh: { on: on(t) } })))).toEqual([
      {
        kind: 'failed_to_light',
        vehicle: 'superheavy',
        engineIds: ['sh_e11', 'sh_e12', 'sh_e13'],
        rings: ['engines_middle'],
        metSeconds: 400,
        runningAfter: 10,
        expected: 13,
      },
      {
        kind: 'engine_out',
        vehicle: 'superheavy',
        engineIds: ['sh_e9', 'sh_e10'],
        rings: ['engines_middle'],
        metSeconds: 410,
        runningAfter: 8,
        expected: 10,
      },
    ]);
  });

  it('boostback with 9 of 10 middle-ring engines lit is failed_to_light', () => {
    const on = schedule(
      [0, SH_ALL],
      [160, INNER],
      [165, [...INNER, ...without(MIDDLE, 'sh_e7')]],
      [200, []],
    );
    const events = detect(timeline(100, 210, (t) => ({ sh: { on: on(t) } })));
    expect(events).toEqual([
      {
        kind: 'failed_to_light',
        vehicle: 'superheavy',
        engineIds: ['sh_e7'],
        rings: ['engines_middle'],
        metSeconds: 165,
        runningAfter: 12,
        expected: 13,
      },
    ]);
  });

  it('boostback relight where 2 engines die 2 s after lighting is died_after_relight', () => {
    const on = schedule(
      [0, SH_ALL],
      [160, INNER],
      [165, [...INNER, ...MIDDLE]],
      [167, [...INNER, ...without(MIDDLE, 'sh_e5', 'sh_e6')]],
      [200, []],
    );
    expect(detect(timeline(100, 210, (t) => ({ sh: { on: on(t) } })))).toEqual([
      {
        kind: 'died_after_relight',
        vehicle: 'superheavy',
        engineIds: ['sh_e5', 'sh_e6'],
        rings: ['engines_middle'],
        metSeconds: 167,
        runningAfter: 11,
        expected: 13,
      },
    ]);
  });

  it('a shutdown inside a panel gap gets a metRange', () => {
    const records = timeline(100, 140, (t) => ({
      sh: t >= 120 && t < 130 ? 'hidden' : { on: t < 120 ? SH_ALL : without(SH_ALL, 'sh_e20') },
    }));
    expect(detect(records)).toEqual([
      {
        kind: 'engine_out',
        vehicle: 'superheavy',
        engineIds: ['sh_e20'],
        rings: ['engines_outer'],
        metSeconds: 130,
        metRange: [119.8, 130],
        runningAfter: 32,
        expected: 33,
      },
    ]);
  });

  it('two engines out 0.5 s apart are one event', () => {
    const on = schedule([0, SH_ALL], [60, without(SH_ALL, 'sh_e20')], [60.6, without(SH_ALL, 'sh_e20', 'sh_e25')]);
    const events = detect(timeline(30, 80, (t) => ({ sh: { on: on(t) } })));
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ engineIds: ['sh_e20', 'sh_e25'], runningAfter: 31, expected: 33, metSeconds: 60 });
  });

  it('push() returns an event only once its step window has closed', () => {
    const on = schedule([0, SH_ALL], [60, without(SH_ALL, 'sh_e20')]);
    const detector = new EngineEventDetector();
    const returnedAt: number[] = [];
    for (const r of timeline(30, 80, (t) => ({ sh: { on: on(t) } }))) {
      if (detector.push(r, exactMet(r)).length > 0) returnedAt.push(exactMet(r));
    }
    expect(returnedAt).toHaveLength(1);
    expect(returnedAt[0]).toBeGreaterThan(62);
    expect(returnedAt[0]).toBeLessThan(63);
    expect(detector.events()).toHaveLength(1);
    detector.reset();
    expect(detector.events()).toEqual([]);
  });

  it('defaults metSeconds to the record MET and ignores records without one', () => {
    const on = schedule([0, SH_ALL], [60, without(SH_ALL, 'sh_e20')]);
    const records = timeline(30, 80, (t) => ({ sh: { on: on(t) } }));
    const noMet = records.map((r) => ({ ...r, mission_elapsed_time: null }));
    expect(detectEngineEvents(noMet)).toEqual([]);
    // Whole-second MET: the change is dated at the first reading of second 60
    expect(detectEngineEvents(records).map((e) => e.metSeconds)).toEqual([60]);
  });
});

describe('ship altitude and landing predicate', () => {
  it('reads the Starship side after separation only', () => {
    const r = record(0, { shipAlt: 12 });
    expect(shipAltitudeKm(r)).toBe(12);
    expect(shipAltitudeKm({ ...r, separation_state: 'pre_separation' })).toBeUndefined();
    expect(shipAltitudeKm({ ...r, altitude_left: { value: 10000, unit: 'FT', status: 'available' } })).toBeCloseTo(3.048);
  });

  it('is a landing burn late or low', () => {
    expect(isShipLandingBurn(3001, undefined)).toBe(true);
    expect(isShipLandingBurn(500, 29)).toBe(true);
    expect(isShipLandingBurn(500, 31)).toBe(false);
    expect(isShipLandingBurn(500, undefined)).toBe(false);
  });
});

const panelArb: fc.Arbitrary<Panel> = fc.oneof(
  fc.constant<Panel>('hidden'),
  fc.record({ on: fc.subarray(SH_ALL), undetected: fc.subarray(SH_ALL, { maxLength: 2 }) }),
);
const shipPanelArb: fc.Arbitrary<Panel> = fc.oneof(
  fc.constant<Panel>('hidden'),
  fc.record({ on: fc.subarray(SS_ALL) }),
);

/** Random flights: runs of identical frames, so states settle and steps form. */
const flightArb = fc
  .array(
    fc.record({ frames: fc.integer({ min: 1, max: 30 }), sh: panelArb, ss: shipPanelArb, alt: fc.option(fc.integer({ min: 0, max: 200 })) }),
    { maxLength: 25 },
  )
  .chain((runs) => fc.record({ runs: fc.constant(runs), start: fc.integer({ min: -20, max: 4000 }) }))
  .map(({ runs, start }) => {
    const records: TelemetryRecord[] = [];
    let t = start;
    for (const run of runs) {
      for (let k = 0; k < run.frames; k++) {
        records.push(record(t, { sh: run.sh, ss: run.ss, shipAlt: run.alt ?? undefined }));
        t = Math.round((t + DT) * 1000) / 1000;
      }
    }
    return records;
  });

describe('EngineEventDetector properties', () => {
  it('never reports events when every engine is undetected', () => {
    fc.assert(
      fc.property(
        fc.array(fc.double({ min: 0.01, max: 3, noNaN: true }), { maxLength: 200 }),
        (steps) => {
          let t = 0;
          const records = steps.map((dt) => record((t += dt), {}));
          return detect(records).length === 0;
        },
      ),
    );
  });

  it('pushing in arbitrary batches gives the same events as detectEngineEvents', () => {
    fc.assert(
      fc.property(flightArb, fc.array(fc.nat(), { maxLength: 10 }), (records, cuts) => {
        const expected = detect(records);
        const bounds = [...new Set(cuts.map((c) => (records.length === 0 ? 0 : c % records.length)))].sort((a, b) => a - b);
        const detector = new EngineEventDetector();
        const seen: EngineEvent[] = [];
        let from = 0;
        for (const cut of [...bounds, records.length]) {
          seen.push(...detector.pushAll(records.slice(from, cut), exactMet));
          from = cut;
        }
        seen.push(...detector.flush());
        expect(seen).toEqual(expected);
        expect(detector.events()).toEqual(expected);
      }),
      { numRuns: 100 },
    );
  });
});
