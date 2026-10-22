/**
 * Stress tests for the engine event detector: sparse frame rates, panel gaps
 * and fades, and whole-flight replays shaped like IFT-12 and IFT-13.
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { TelemetryRecord } from '../../core/types';
import { enginesInRing, enginesOf } from './layout';
import { EngineEventDetector, detectEngineEvents } from './detector';
import type { EngineEvent, EngineStatusMap } from './events';
import { eventsForSeries } from '../../features/graphs/plugins/engine-markers';

const ids = (engines: readonly { id: string }[]) => engines.map((e) => e.id);
const SH_ALL = ids(enginesOf('superheavy'));
const SS_ALL = ids(enginesOf('starship'));
const INNER = ids(enginesInRing('superheavy', 'engines_inner'));
const MIDDLE = ids(enginesInRing('superheavy', 'engines_middle'));
const OUTER = ids(enginesInRing('superheavy', 'engines_outer'));
const SEA_LEVEL = ids(enginesInRing('starship', 'atmo'));
const without = (all: readonly string[], ...out: string[]) => all.filter((id) => !out.includes(id));
const MECO5 = [...INNER, 'sh_e4', 'sh_e9'];

type Panel = 'hidden' | { on: readonly string[]; undetected?: readonly string[] };
interface Frame { sh?: Panel; ss?: Panel; shipAlt?: number }

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

/** Frames every dt seconds from `from` (inclusive) to `to` (exclusive). */
function sampled(from: number, to: number, dt: number, frame: (t: number) => Frame): TelemetryRecord[] {
  const records: TelemetryRecord[] = [];
  const n = Math.ceil((to - from) / dt - 1e-9);
  for (let k = 0; k < n; k++) {
    const t = Math.round((from + k * dt) * 1000) / 1000;
    records.push(record(t, frame(t)));
  }
  return records;
}

const exactMet = (r: TelemetryRecord) => r.timestamp / 1000;
const detect = (records: TelemetryRecord[]) => detectEngineEvents(records, exactMet);

function schedule(...steps: [number, readonly string[]][]): (t: number) => readonly string[] {
  return (t) => {
    let on = steps[0][1];
    for (const [at, list] of steps) if (t >= at - 1e-9) on = list;
    return on;
  };
}

/** Engines shut down one by one at the given times; the rest stay on. */
function staggered(all: readonly string[], offAt: ReadonlyMap<string, number>): (t: number) => readonly string[] {
  return (t) => all.filter((id) => !(offAt.has(id) && t >= offAt.get(id)! - 1e-9));
}

/** V2 MECO 33 -> 3 spread over 3 s: outer ring over 160..161.4, middle over 161.5..162.85. */
const MECO3_OFF = new Map<string, number>([
  ...OUTER.map((id, k): [string, number] => [id, 160 + k * 0.075]),
  ...MIDDLE.map((id, k): [string, number] => [id, 161.5 + k * 0.15]),
]);
/** V3 MECO 33 -> 5 spread over 3 s. */
const MECO5_OFF = new Map<string, number>(
  without(SH_ALL, ...MECO5).map((id, k): [string, number] => [id, 160 + k * (2.9 / 27)]),
);

describe('sparse frame rates', () => {
  const rates = [
    { dt: 1, phases: [0, 0.25, 0.5, 0.75] },
    { dt: 3, phases: [0, 1, 2] },
  ];

  for (const { dt, phases } of rates) {
    for (const phase of phases) {
      it(`33 -> 32 yields exactly one engine_out at 1 record per ${dt} s (phase ${phase})`, () => {
        const on = schedule([0, SH_ALL], [102, without(SH_ALL, 'sh_e20')]);
        const records = sampled(30 + phase, 140, dt, (t) => ({ sh: { on: on(t) } }));
        const firstOff = records.map(exactMet).find((t) => t >= 102)!;
        expect(detect(records)).toEqual([
          {
            kind: 'engine_out',
            vehicle: 'superheavy',
            engineIds: ['sh_e20'],
            rings: ['engines_outer'],
            metSeconds: firstOff,
            runningAfter: 32,
            expected: 33,
          },
        ]);
      });

      it(`staggered V2 MECO 33 -> 3 over 3 s gives no event at 1 record per ${dt} s (phase ${phase})`, () => {
        const on = staggered(SH_ALL, MECO3_OFF);
        expect(detect(sampled(100 + phase, 180, dt, (t) => ({ sh: { on: on(t) } })))).toEqual([]);
      });

      it(`staggered V3 MECO 33 -> 5 over 3 s gives no event at 1 record per ${dt} s (phase ${phase})`, () => {
        const on = staggered(SH_ALL, MECO5_OFF);
        expect(detect(sampled(100 + phase, 180, dt, (t) => ({ sh: { on: on(t) } })))).toEqual([]);
      });
    }
  }

  it('property: any MECO 33 -> 3 staggered continuously (no lull > 1 s) over <= 3 s is silent at 1 record/s', () => {
    // Shutdown times in random engine order: gaps of at most 0.25 s, all within 3 s
    const offsetsArb = fc
      .tuple(
        fc.array(fc.double({ min: 0, max: 0.25, noNaN: true }), { minLength: 30, maxLength: 30 }),
        fc.shuffledSubarray(without(SH_ALL, ...INNER), { minLength: 30, maxLength: 30 }),
      )
      .map(([gaps, order]) => {
        let at = 0;
        return new Map(order.map((id, k): [string, number] => [id, 160 + Math.min(3, (at += k === 0 ? 0 : gaps[k]))]));
      });
    fc.assert(
      fc.property(
        offsetsArb,
        fc.double({ min: 0, max: 0.999, noNaN: true }),
        (offAt, phase) => {
          const on = staggered(SH_ALL, offAt);
          return detect(sampled(100 + phase, 180, 1, (t) => ({ sh: { on: on(t) } }))).length === 0;
        },
      ),
      { numRuns: 200 },
    );
  });

  it('property: a MECO with a lull of up to 1 s between shutdowns is silent at 1 record/s', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 29 }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 0.999, noNaN: true }),
        (split, lull, phase) => {
          const order = without(SH_ALL, ...INNER);
          const offAt = new Map(order.map((id, k): [string, number] => [id, 160 + (k < split ? 0 : lull)]));
          const on = staggered(SH_ALL, offAt);
          return detect(sampled(100 + phase, 180, 1, (t) => ({ sh: { on: on(t) } }))).length === 0;
        },
      ),
      { numRuns: 200 },
    );
  });

  it('MECO 33 -> 3 with 27 engines off at T+160 and the last 3 at T+162 (2 s lull) gives no event at 1 record/s', () => {
    // fast-check counterexample of the unrestricted property, simplified
    const order = without(SH_ALL, ...INNER);
    const offAt = new Map(order.map((id, k): [string, number] => [id, k < 27 ? 160 : 162.000001]));
    const on = staggered(SH_ALL, offAt);
    expect(detect(sampled(100, 180, 1, (t) => ({ sh: { on: on(t) } })))).toEqual([]);
  });

  it('a real 33 -> 32 a few seconds before a staggered MECO is still reported once at 1 record/s', () => {
    const offAt = new Map(MECO5_OFF);
    offAt.set('sh_e20', 150);
    const on = staggered(SH_ALL, offAt);
    const events = detect(sampled(100, 180, 1, (t) => ({ sh: { on: on(t) } })));
    expect(events.map((e) => [e.kind, e.engineIds, e.metSeconds, e.runningAfter, e.expected])).toEqual([
      ['engine_out', ['sh_e20'], 150, 32, 33],
    ]);
  });
});

describe('gaps with a pending shutdown', () => {
  it('an engine read off once right before a long gap and still off after it: one engine_out, no metRange', () => {
    const records = sampled(30, 200, 1, (t) => {
      if (t >= 120 && t < 150) return { sh: 'hidden' };
      return { sh: { on: t < 119 ? SH_ALL : without(SH_ALL, 'sh_e20') } };
    });
    const detector = new EngineEventDetector();
    for (const r of records) {
      const events = detector.push(r, exactMet(r));
      // Nothing can be finalized while the panel is hidden
      if (exactMet(r) < 150) expect(events).toEqual([]);
    }
    detector.flush();
    expect(detector.events()).toEqual([
      {
        kind: 'engine_out',
        vehicle: 'superheavy',
        engineIds: ['sh_e20'],
        rings: ['engines_outer'],
        metSeconds: 119,
        runningAfter: 32,
        expected: 33,
      },
    ]);
  });

  it('an engine read off once right before a long gap that is back on after it: no event', () => {
    const records = sampled(30, 200, 1, (t) => {
      if (t >= 120 && t < 150) return { sh: 'hidden' };
      return { sh: { on: t === 119 ? without(SH_ALL, 'sh_e20') : SH_ALL } };
    });
    expect(detect(records)).toEqual([]);
  });

  it('an open step with a pending shutdown across a gap joins the step once the panel returns', () => {
    const records = sampled(30, 120, 1, (t) => {
      if (t >= 62 && t < 80) return { sh: 'hidden' };
      return { sh: { on: t < 60 ? SH_ALL : t < 61 ? without(SH_ALL, 'sh_e20') : without(SH_ALL, 'sh_e20', 'sh_e25') } };
    });
    expect(detect(records)).toEqual([
      {
        kind: 'engine_out',
        vehicle: 'superheavy',
        engineIds: ['sh_e20', 'sh_e25'],
        rings: ['engines_outer'],
        metSeconds: 60,
        runningAfter: 31,
        expected: 33,
      },
    ]);
  });

  it('an open step whose pending engine is back on after the gap closes with just the confirmed engine', () => {
    const records = sampled(30, 120, 1, (t) => {
      if (t >= 62 && t < 80) return { sh: 'hidden' };
      return { sh: { on: t >= 60 && t < 61 ? without(SH_ALL, 'sh_e20') : t === 61 ? without(SH_ALL, 'sh_e20', 'sh_e25') : t < 60 ? SH_ALL : without(SH_ALL, 'sh_e20') } };
    });
    expect(detect(records).map((e) => [e.kind, e.engineIds, e.metSeconds, e.runningAfter])).toEqual([
      ['engine_out', ['sh_e20'], 60, 32],
    ]);
  });

  it('a gap that ends the data with a pending shutdown reports nothing for the pending engine', () => {
    const records = sampled(30, 150, 1, (t) => {
      if (t >= 120) return { sh: 'hidden' };
      return { sh: { on: t < 119 ? SH_ALL : without(SH_ALL, 'sh_e20') } };
    });
    expect(detect(records)).toEqual([]);
  });
});

describe('panel fade-out / fade-in during a burn', () => {
  /** All engines read 'inactive' for `frames` records before and after a gap. */
  function fade(t: number, gapFrom: number, gapTo: number, dt: number, frames: number): 'hidden' | 'dark' | null {
    if (t >= gapFrom && t < gapTo) return 'hidden';
    if (t >= gapFrom - frames * dt - 1e-9 && t < gapFrom) return 'dark';
    if (t >= gapTo && t < gapTo + frames * dt - 1e-9) return 'dark';
    return null;
  }

  for (const { dt, frames } of [{ dt: 1, frames: 1 }, { dt: 0.2, frames: 3 }]) {
    it(`Super Heavy 33 running, fade-out only (${frames} dark frame(s) at ${dt} s): no event`, () => {
      const records = sampled(30, 200, dt, (t) => {
        const f = fade(t, 120, 130, dt, frames);
        return { sh: f === 'hidden' ? 'hidden' : f === 'dark' && t < 120 ? { on: [] } : { on: SH_ALL } };
      });
      expect(detect(records)).toEqual([]);
    });

    it(`Super Heavy 33 running, fade-out and fade-in (${frames} dark frame(s) each side at ${dt} s): no event`, () => {
      const records = sampled(30, 200, dt, (t) => {
        const f = fade(t, 120, 130, dt, frames);
        return { sh: f === 'hidden' ? 'hidden' : f === 'dark' ? { on: [] } : { on: SH_ALL } };
      });
      expect(detect(records)).toEqual([]);
    });

    it(`Super Heavy with an engine already out: a fade-out/fade-in adds no failed_to_light (${dt} s)`, () => {
      const running = (t: number) => (t < 60 ? SH_ALL : without(SH_ALL, 'sh_e20'));
      const records = sampled(30, 200, dt, (t) => {
        const f = fade(t, 120, 130, dt, frames);
        return { sh: f === 'hidden' ? 'hidden' : f === 'dark' ? { on: [] } : { on: running(t) } };
      });
      expect(detect(records).map((e) => [e.kind, e.engineIds, e.metSeconds])).toEqual([
        ['engine_out', ['sh_e20'], 60],
      ]);
    });

    it(`Starship mid-ascent fade-out/fade-in then a real engine out 4 s later is engine_out, not died_after_relight (${dt} s)`, () => {
      const running = schedule([0, []], [144, SS_ALL], [404, without(SS_ALL, 'ss_e4')], [553, []]);
      const records = sampled(130, 600, dt, (t) => {
        const f = fade(t, 300, 400, dt, frames);
        return { ss: f === 'hidden' ? 'hidden' : f === 'dark' ? { on: [] } : { on: running(t) } };
      });
      expect(detect(records).map((e) => [e.kind, e.engineIds])).toEqual([['engine_out', ['ss_e4']]]);
    });

    it(`Starship with a vacuum engine out: a fade-out/fade-in adds no failed_to_light (${dt} s)`, () => {
      const running = schedule([0, []], [144, SS_ALL], [190, without(SS_ALL, 'ss_e5')], [553, []]);
      const records = sampled(130, 600, dt, (t) => {
        const f = fade(t, 300, 310, dt, frames);
        return { ss: f === 'hidden' ? 'hidden' : f === 'dark' ? { on: [] } : { on: running(t) } };
      });
      expect(detect(records).map((e) => [e.kind, e.engineIds, e.metSeconds])).toEqual([
        ['engine_out', ['ss_e5'], 190],
      ]);
    });
  }
});

// ---------------------------------------------------------------------------
// Whole-flight replays at 1 record/s
// ---------------------------------------------------------------------------

const BB_OUTER = ['sh_e21', 'sh_e22', 'sh_e23', 'sh_e24', 'sh_e25', 'sh_e26', 'sh_e27'];
const BB_SURVIVORS = [...INNER, 'sh_e4', 'sh_e9', 'sh_e11', 'sh_e12', 'sh_e13'];

/** IFT-12-like: ascent engine out, MECO to 5, boostback relight where most die, ship vacuum out, ship landing 2 of 3. */
function ift12(): TelemetryRecord[] {
  const sh = schedule(
    [-5, SH_ALL],
    [102, without(SH_ALL, 'sh_e20')],
    [142, MECO5],
    [150, [...INNER, ...MIDDLE, ...BB_OUTER]], // 20 lit
    [153, BB_SURVIVORS], // 12 die 3 s after the relight
    [200, []],
    [400, [...INNER, ...MIDDLE]],
    [410, MECO5],
    [415, INNER],
    [425, []],
  );
  const ss = schedule(
    [-5, []],
    [144, SS_ALL],
    [190, without(SS_ALL, 'ss_e5')],
    [553, []],
    // No in-space relight
    [3906, ['ss_e1', 'ss_e2']],
    [3916, ['ss_e1']],
    [3925, []],
  );
  return sampled(-5, 3940, 1, (t) => ({
    sh: t >= 430 ? 'hidden' : { on: sh(t) },
    ss: t >= 600 && t < 3890 ? 'hidden' : { on: ss(t) },
  }));
}

const IFT12_EVENTS: EngineEvent[] = [
  {
    kind: 'engine_out',
    vehicle: 'superheavy',
    engineIds: ['sh_e20'],
    rings: ['engines_outer'],
    metSeconds: 102,
    runningAfter: 32,
    expected: 33,
  },
  {
    kind: 'died_after_relight',
    vehicle: 'superheavy',
    engineIds: ['sh_e5', 'sh_e6', 'sh_e7', 'sh_e8', 'sh_e10', ...BB_OUTER],
    rings: ['engines_middle', 'engines_outer'],
    metSeconds: 153,
    runningAfter: 8,
    expected: 20,
  },
  {
    kind: 'engine_out',
    vehicle: 'starship',
    engineIds: ['ss_e5'],
    rings: ['vacuum'],
    metSeconds: 190,
    runningAfter: 5,
    expected: 6,
  },
  {
    kind: 'failed_to_light',
    vehicle: 'starship',
    engineIds: ['ss_e3'],
    rings: ['atmo'],
    metSeconds: 3906,
    runningAfter: 2,
    expected: 3,
  },
];

/** IFT-13-like: clean ascent, MECO to 5, boostback on all 33, landing 10 of 13 -> 8 -> 5. */
function ift13(): TelemetryRecord[] {
  const lit = [...INNER, ...MIDDLE.slice(0, 7)]; // e1-e10
  const sh = schedule(
    [-5, SH_ALL],
    [142, MECO5],
    [150, SH_ALL],
    [200, []],
    [400, lit],
    [410, without(lit, 'sh_e9', 'sh_e10')],
    [415, [...INNER, 'sh_e4', 'sh_e5']],
    [425, []],
  );
  const ss = schedule(
    [-5, []],
    [144, SS_ALL],
    [520, []],
    [3906, SEA_LEVEL],
    [3916, ['ss_e1', 'ss_e2']],
    [3920, ['ss_e1']],
    [3925, []],
  );
  return sampled(-5, 3940, 1, (t) => ({
    sh: t >= 430 ? 'hidden' : { on: sh(t) },
    ss: t >= 600 && t < 3890 ? 'hidden' : { on: ss(t) },
  }));
}

const IFT13_EVENTS: EngineEvent[] = [
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
];

describe('flight replays at 1 record/s', () => {
  it('IFT-12-like timeline gives the exact event list', () => {
    expect(detect(ift12())).toEqual(IFT12_EVENTS);
  });

  it('IFT-13-like timeline gives the exact event list', () => {
    expect(detect(ift13())).toEqual(IFT13_EVENTS);
  });

  it('whole-second MET (no metSecondsOf) gives the same IFT-12 events', () => {
    expect(detectEngineEvents(ift12())).toEqual(IFT12_EVENTS);
  });

  it('IFT-12 incremental pushes report each event within ~3 s of it, never twice', () => {
    const detector = new EngineEventDetector();
    const at: [number, number][] = [];
    for (const r of ift12()) {
      for (const e of detector.push(r, exactMet(r))) at.push([e.metSeconds, exactMet(r)]);
    }
    expect(at.map(([event]) => event)).toEqual(IFT12_EVENTS.map((e) => e.metSeconds));
    for (const [event, pushed] of at) {
      expect(pushed - event).toBeGreaterThan(0);
      expect(pushed - event).toBeLessThanOrEqual(4);
    }
    expect(detector.flush()).toEqual([]);
  });

  it('chart selection on the IFT-12 events (separation at T+144)', () => {
    const events = detect(ift12());
    const sep = 144;
    expect(eventsForSeries(events, 'superheavy', sep)).toEqual(IFT12_EVENTS.filter((e) => e.vehicle === 'superheavy'));
    // The boostback died_after_relight (after separation) stays off the Starship charts
    expect(eventsForSeries(events, 'starship', sep)).toEqual([IFT12_EVENTS[0], IFT12_EVENTS[2], IFT12_EVENTS[3]]);
  });

  /**
   * A ship SECO where the sea-level engines cut at T+489 and the vacuum
   * engines at T+553 shuts down whole rings, so it is planned even though 2
   * running is not a planned Starship count.
   */
  it('split SECO (sea-level at T+489, vacuum at T+553) adds no event', () => {
    const ss = schedule([-5, []], [144, SS_ALL], [190, without(SS_ALL, 'ss_e5')], [489, ['ss_e4', 'ss_e6']], [553, []]);
    const events = detect(sampled(130, 600, 1, (t) => ({ ss: { on: ss(t) } })));
    expect(events.map((e) => [e.kind, e.engineIds, e.metSeconds, e.runningAfter, e.expected])).toEqual([
      ['engine_out', ['ss_e5'], 190, 5, 6],
    ]);
  });
});

describe('eventsForSeries properties', () => {
  const eventArb: fc.Arbitrary<EngineEvent> = fc.record({
    kind: fc.constantFrom('engine_out' as const, 'died_after_relight' as const, 'failed_to_light' as const),
    vehicle: fc.constantFrom('superheavy' as const, 'starship' as const),
    engineIds: fc.constant(['sh_e1']),
    rings: fc.constant(['engines_inner']),
    metSeconds: fc.double({ min: -10, max: 5000, noNaN: true }),
    runningAfter: fc.nat(33),
    expected: fc.nat(33),
  });

  it('never shows Starship events on Super Heavy charts, nor Super Heavy events after separation on Starship charts', () => {
    fc.assert(
      fc.property(fc.array(eventArb, { maxLength: 30 }), fc.double({ min: 0, max: 600, noNaN: true }), (events, sep) => {
        const sh = eventsForSeries(events, 'superheavy', sep);
        const ss = eventsForSeries(events, 'starship', sep);
        expect(sh.every((e) => e.vehicle === 'superheavy')).toBe(true);
        expect(ss.every((e) => e.vehicle === 'starship' || e.metSeconds < sep)).toBe(true);
        // Nothing is lost: every Super Heavy event is on the SH charts, every ship event on the ship charts
        expect(sh).toEqual(events.filter((e) => e.vehicle === 'superheavy'));
        expect(ss.filter((e) => e.vehicle === 'starship')).toEqual(events.filter((e) => e.vehicle === 'starship'));
      }),
    );
  });
});
