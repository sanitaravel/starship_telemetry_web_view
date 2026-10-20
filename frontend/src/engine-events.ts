/**
 * Engine failure detection from per-frame engine statuses.
 *
 * Pure logic, no DOM: feed records in sequence order to an
 * EngineEventDetector and it reports engines that shut down unexpectedly
 * (engine_out), died shortly after a relight (died_after_relight) or did not
 * light with the rest of their ring (failed_to_light).
 *
 * Each vehicle is tracked independently:
 * - A vehicle's panel is visible in a record iff at least one of its engines
 *   reads 'active' or 'inactive'. Fully 'undetected' records are a gap and
 *   carry no information; a single 'undetected' engine on a visible panel
 *   keeps its previous state.
 * - An engine's on/off state is debounced: it changes only after
 *   CONFIRM_FRAMES consecutive visible readings of the new state spanning at
 *   least CONFIRM_MIN_S. The change is dated at the first of those readings.
 *   A change can only be confirmed across a panel gap with CONFIRM_FRAMES
 *   readings after it, and frames with no engine active next to a gap (the
 *   panel fading out or in) count as part of the gap.
 * - Shutdowns within the step window of each other form one step (the
 *   window grows with sparse sampling). A step that leaves a planned running
 *   count (MECO, landing burn throttle-down, shutdown), or on Starship shuts
 *   down whole rings (staggered SECO), is not an event; any other step is one
 *   event.
 */

import type { TelemetryRecord } from './types';
import {
  engineById,
  enginesInRing,
  enginesOf,
  type EngineRing,
  type Vehicle,
} from './engine-layout';
import { parseMETToSeconds } from './time-series-graphs/series-math';

export type { Vehicle };

export type EngineEventKind = 'engine_out' | 'died_after_relight' | 'failed_to_light';

export interface EngineEvent {
  kind: EngineEventKind;
  vehicle: Vehicle;
  /** Engines involved, in layout order. */
  engineIds: string[];
  /** Rings (SVG subgroup ids) of those engines, in layout order, no duplicates. */
  rings: string[];
  /** MET of the event in seconds (for failed_to_light, the burn start). */
  metSeconds: number;
  /** Set when the change happened inside a panel gap: [last visible before, first visible after]. */
  metRange?: [number, number];
  /** Engines running after the event. */
  runningAfter: number;
  /** Engines that should be running at that moment, for the "n/m running" text. */
  expected: number;
}

export type EngineStatus = 'active' | 'inactive' | 'undetected';
export type EngineStatusMap = Record<string, EngineStatus>;

/** Detection thresholds. Times are seconds of MET. */
export interface EngineEventConfig {
  /** Consecutive visible readings needed to confirm a state change. */
  CONFIRM_FRAMES: number;
  /** Minimum time those readings must span. */
  CONFIRM_MIN_S: number;
  /** Shutdowns closer than this to the previous one join the same step (minimum window). */
  STEP_WINDOW_S: number;
  /**
   * The step window is at least this many times the recent spacing of
   * visible records, so sparse sampling does not split a staggered MECO.
   */
  STEP_WINDOW_SAMPLES: number;
  /** Visible-record spacings kept per vehicle for the running median. */
  SPACING_HISTORY: number;
  /**
   * Frames with no engine active right before or after a panel gap are the
   * panel fading out or in, not data, while their run is at most this long.
   */
  FADE_MAX_S: number;
  /** An engine_out this soon after a (non-first) burn starts is died_after_relight. */
  RELIGHT_GRACE_S: number;
  /** Engines that have not lit this long after a burn starts failed to light. */
  IGNITION_WINDOW_S: number;
  /**
   * A first burn starting no later than this is the liftoff burn, which is
   * never checked for failed_to_light (startup is staggered on the pad).
   */
  LIFTOFF_MAX_MET_S: number;
  /** A ship burn starting after this MET is the landing burn. */
  SHIP_LANDING_MIN_MET_S: number;
  /** A ship burn starting below this altitude (km) is the landing burn. */
  SHIP_LANDING_MAX_ALTITUDE_KM: number;
  /** Running counts a shutdown step may leave without being an event. */
  PLANNED_COUNTS: Record<Vehicle, readonly number[]>;
  /** Extra planned counts during the ship's landing burn. */
  SHIP_LANDING_PLANNED_COUNTS: readonly number[];
}

export const ENGINE_EVENT_CONFIG: Readonly<EngineEventConfig> = {
  CONFIRM_FRAMES: 2,
  CONFIRM_MIN_S: 0.5,
  STEP_WINDOW_S: 3,
  STEP_WINDOW_SAMPLES: 2.5,
  SPACING_HISTORY: 9,
  FADE_MAX_S: 1,
  RELIGHT_GRACE_S: 5,
  IGNITION_WINDOW_S: 3,
  LIFTOFF_MAX_MET_S: 30,
  SHIP_LANDING_MIN_MET_S: 3000,
  SHIP_LANDING_MAX_ALTITUDE_KM: 30,
  PLANNED_COUNTS: { superheavy: [13, 5, 3, 0], starship: [0] },
  SHIP_LANDING_PLANNED_COUNTS: [2, 1],
};

/**
 * Whether a ship burn is its landing burn: it starts late in the flight or
 * low enough (altitude in km, when known).
 */
export function isShipLandingBurn(
  startMetSeconds: number,
  shipAltitudeKm: number | undefined,
  config: EngineEventConfig = ENGINE_EVENT_CONFIG,
): boolean {
  if (startMetSeconds > config.SHIP_LANDING_MIN_MET_S) return true;
  return shipAltitudeKm !== undefined && shipAltitudeKm < config.SHIP_LANDING_MAX_ALTITUDE_KM;
}

const ALTITUDE_TO_KM: Record<string, number> = { KM: 1, MI: 1.609344, FT: 0.0003048 };

/**
 * The ship's altitude in km from a record: after separation, the reading on
 * the side assigned to Starship. Undefined before separation or when unread.
 */
export function shipAltitudeKm(record: TelemetryRecord): number | undefined {
  if (record.separation_state !== 'post_separation') return undefined;
  for (const side of ['left', 'right'] as const) {
    if (record[`stage_assignment_${side}`] !== 'starship') continue;
    const reading = record[`altitude_${side}`];
    if (reading.status !== 'available' || reading.value === null) continue;
    const factor = ALTITUDE_TO_KM[(reading.unit ?? 'KM').toUpperCase()];
    if (factor !== undefined) return reading.value * factor;
  }
  return undefined;
}

/** A not yet confirmed change of one engine. */
interface Candidate {
  on: boolean;
  firstT: number;
  count: number;
  /** Set when the first reading was the first visible record after a gap. */
  range?: [number, number];
  /** Readings since the latest gap, when the candidate started before it. */
  afterGap?: number;
}

/** One record's statuses for a vehicle. */
interface Frame {
  t: number;
  statuses: EngineStatusMap;
}

interface Change {
  id: string;
  on: boolean;
  t: number;
  range?: [number, number];
}

interface Burn {
  start: number;
  /** 0 for the vehicle's first burn, counting relight segments. */
  index: number;
  /** Already running when the baseline was established. */
  atBaseline: boolean;
  /** Started inside a panel gap (start time is the first visible record after it). */
  startRange?: [number, number];
  /** Ship landing burn (extra planned counts). */
  landing: boolean;
  /** Engines running at the start or lit within the ignition window. */
  lit: Set<string>;
  ignitionChecked: boolean;
}

interface Step {
  burn: Burn | null;
  /** Running count before the first shutdown of the step. */
  before: number;
  /** Running count after the latest shutdown of the step. */
  after: number;
  offs: string[];
  firstT: number;
  lastT: number;
  range?: [number, number];
}

class VehicleTracker {
  private readonly ids: string[];
  private readonly order: Map<string, number>;
  /** Debounced state, true = on. Absent until first confirmed. */
  private state = new Map<string, boolean>();
  private candidates = new Map<string, Candidate>();
  /** Engines with at least one reading, before the baseline. */
  private seen = new Set<string>();
  private baseline = false;
  private lastVisibleT: number | null = null;
  private inGap = false;
  private running = 0;
  private burn: Burn | null = null;
  private burnCount = 0;
  /** A shutdown left a planned non-zero count; the next ignition starts a new segment. */
  private segmentEnded = false;
  private step: Step | null = null;
  private ignitionPending: Burn[] = [];
  private shipAltitude: number | undefined;
  /** Frames with no engine active, held until it is known whether a gap follows. */
  private fadeOut: Frame[] = [];
  /** Time of the first no-engine-active frame right after a gap, while treated as gap. */
  private fadeInStart: number | null = null;
  /** Recent spacings between consecutive visible records (no gap in between). */
  private spacings: number[] = [];

  constructor(
    private readonly vehicle: Vehicle,
    private readonly config: EngineEventConfig,
  ) {
    const engines = enginesOf(vehicle);
    this.ids = engines.map((e) => e.id);
    this.order = new Map(this.ids.map((id, i) => [id, i]));
  }

  push(t: number, statuses: EngineStatusMap, shipAltitude?: number): EngineEvent[] {
    if (shipAltitude !== undefined) this.shipAltitude = shipAltitude;

    const visible = this.ids.some((id) => statuses[id] === 'active' || statuses[id] === 'inactive');
    if (!visible) {
      // Dark frames right before the gap were the panel fading out
      this.fadeOut = [];
      this.fadeInStart = null;
      this.enterGap();
      return this.finalize(t, false);
    }

    const dark = !this.ids.some((id) => statuses[id] === 'active');
    if (dark && this.inGap) {
      // Panel fading in: part of the gap while the dark run is short
      this.fadeInStart ??= t;
      if (t - this.fadeInStart <= this.config.FADE_MAX_S) return this.finalize(t, false);
    } else if (dark) {
      // Maybe fading out: hold until a gap or an active frame shows which
      this.fadeOut.push({ t, statuses });
      const events: EngineEvent[] = [];
      while (this.fadeOut.length > 0 && t - this.fadeOut[0].t > this.config.FADE_MAX_S) {
        events.push(...this.process(this.fadeOut.shift()!));
      }
      return events;
    }

    const events = this.processFadeOut();
    events.push(...this.process({ t, statuses }));
    return events;
  }

  /** Finalize everything that can be: the open step, and ignition checks whose window has passed. */
  flush(t: number | null): EngineEvent[] {
    // No more records: held dark frames were not followed by a gap
    const events = this.processFadeOut();
    events.push(...this.finalize(t ?? Number.NEGATIVE_INFINITY, true));
    return events;
  }

  private processFadeOut(): EngineEvent[] {
    const events: EngineEvent[] = [];
    for (const frame of this.fadeOut) events.push(...this.process(frame));
    this.fadeOut = [];
    return events;
  }

  private enterGap(): void {
    if (this.lastVisibleT === null || this.inGap) return;
    this.inGap = true;
    // Readings before the gap alone cannot confirm a change across it
    for (const candidate of this.candidates.values()) candidate.afterGap = 0;
  }

  /** Step window: STEP_WINDOW_S, or more when records are sparse. */
  private stepWindow(): number {
    if (this.spacings.length === 0) return this.config.STEP_WINDOW_S;
    const sorted = [...this.spacings].sort((a, b) => a - b);
    const mid = sorted.length >> 1;
    const median = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    return Math.max(this.config.STEP_WINDOW_S, this.config.STEP_WINDOW_SAMPLES * median);
  }

  /** Process one visible frame. */
  private process({ t, statuses }: Frame): EngineEvent[] {
    this.fadeInStart = null;
    const edge: [number, number] | undefined =
      this.inGap && this.lastVisibleT !== null ? [this.lastVisibleT, t] : undefined;
    if (!this.inGap && this.lastVisibleT !== null && t > this.lastVisibleT) {
      this.spacings.push(t - this.lastVisibleT);
      if (this.spacings.length > this.config.SPACING_HISTORY) this.spacings.shift();
    }
    this.inGap = false;
    this.lastVisibleT = t;

    const changes = this.debounce(t, statuses, edge);

    if (!this.baseline) {
      for (const id of this.ids) {
        if (statuses[id] === 'active' || statuses[id] === 'inactive') this.seen.add(id);
      }
      if ([...this.seen].every((id) => this.state.has(id))) this.establishBaseline(t);
      return this.finalize(t, false);
    }

    // Shutdowns before ignitions at the same time, so a step's count is net of them
    changes.sort((a, b) => a.t - b.t || Number(a.on) - Number(b.on));
    const events: EngineEvent[] = [];
    for (const change of changes) {
      if (change.on) this.applyOn(change);
      else events.push(...this.applyOff(change));
    }
    events.push(...this.finalize(t, false));
    return events;
  }

  /** Update debounced states from one visible record; returns confirmed changes. */
  private debounce(t: number, statuses: EngineStatusMap, edge: [number, number] | undefined): Change[] {
    const changes: Change[] = [];
    for (const id of this.ids) {
      const reading = statuses[id];
      if (reading !== 'active' && reading !== 'inactive') continue;
      const on = reading === 'active';
      const previous = this.state.get(id);
      if (previous === on) {
        this.candidates.delete(id);
        continue;
      }
      let candidate = this.candidates.get(id);
      if (!candidate || candidate.on !== on) {
        candidate = { on, firstT: t, count: 0, range: edge };
        this.candidates.set(id, candidate);
      }
      candidate.count++;
      if (candidate.afterGap !== undefined) candidate.afterGap++;
      if (
        candidate.count >= this.config.CONFIRM_FRAMES &&
        t - candidate.firstT >= this.config.CONFIRM_MIN_S &&
        (candidate.afterGap === undefined || candidate.afterGap >= this.config.CONFIRM_FRAMES)
      ) {
        this.candidates.delete(id);
        this.state.set(id, on);
        // After the baseline, an engine never read before counts as off
        if (previous === undefined && !on) continue;
        changes.push({ id, on, t: candidate.firstT, range: candidate.range });
      }
    }
    return changes;
  }

  private establishBaseline(t: number): void {
    this.baseline = true;
    this.running = this.ids.filter((id) => this.state.get(id) === true).length;
    if (this.running > 0) {
      this.burn = this.newBurn(t, true, undefined);
    }
  }

  private newBurn(start: number, atBaseline: boolean, startRange: [number, number] | undefined): Burn {
    const lit = new Set(this.ids.filter((id) => this.state.get(id) === true));
    const burn: Burn = {
      start,
      index: this.burnCount++,
      atBaseline,
      startRange,
      landing:
        this.vehicle === 'starship' && isShipLandingBurn(start, this.shipAltitude, this.config),
      lit,
      ignitionChecked: false,
    };
    if (!atBaseline) this.ignitionPending.push(burn);
    return burn;
  }

  private applyOn(change: Change): void {
    if (this.running === 0 || this.segmentEnded || !this.burn) {
      // Not yet on: newBurn's lit set comes from the state, which already has it
      this.burn = this.newBurn(change.t, false, change.range);
      this.segmentEnded = false;
    }
    this.running++;
    if (change.t <= this.burn.start + this.config.IGNITION_WINDOW_S) this.burn.lit.add(change.id);
  }

  private applyOff(change: Change): EngineEvent[] {
    const events: EngineEvent[] = [];
    this.running = Math.max(0, this.running - 1);
    const step = this.step;
    if (step && change.t - step.lastT <= this.stepWindow()) {
      step.offs.push(change.id);
      step.lastT = Math.max(step.lastT, change.t);
      step.after = this.running;
      step.range ??= change.range;
    } else {
      if (step) events.push(...this.closeStep());
      this.step = {
        burn: this.burn,
        before: this.running + 1,
        after: this.running,
        offs: [change.id],
        firstT: change.t,
        lastT: change.t,
        range: change.range,
      };
    }
    if (this.running === 0) {
      this.burn = null;
      this.segmentEnded = false;
    } else {
      this.segmentEnded = this.isPlanned(this.running, this.burn);
    }
    return events;
  }

  private isPlanned(count: number, burn: Burn | null): boolean {
    if (this.config.PLANNED_COUNTS[this.vehicle].includes(count)) return true;
    return this.vehicle === 'starship' && burn !== null && burn.landing
      && this.config.SHIP_LANDING_PLANNED_COUNTS.includes(count);
  }

  /**
   * Starship staggered SECO: a step whose engines all belong to rings left
   * with nothing running (e.g. the sea-level ring cut before the vacuum one)
   * is planned whatever the remaining count. Super Heavy uses counts only.
   */
  private isRingShutdown(step: Step): boolean {
    if (this.vehicle !== 'starship') return false;
    const rings = new Set(step.offs.map((id) => engineById(id)!.ring));
    return [...rings].every((ring) =>
      enginesInRing(this.vehicle, ring).every((e) => this.state.get(e.id) !== true),
    );
  }

  /** Whether an engine may still confirm a change (on/off) dated no later than `limit`. */
  private hasPending(on: boolean, limit: number): boolean {
    for (const [id, candidate] of this.candidates) {
      if (candidate.on !== on || candidate.firstT > limit) continue;
      // Only real transitions: off->on, or on->off
      if ((this.state.get(id) === true) !== on) return true;
    }
    return false;
  }

  private finalize(now: number, force: boolean): EngineEvent[] {
    const events: EngineEvent[] = [];
    const step = this.step;
    if (step) {
      const windowEnd = step.lastT + this.stepWindow();
      if (force || (now > windowEnd && !this.hasPending(false, windowEnd))) {
        events.push(...this.closeStep());
      }
    }
    this.ignitionPending = this.ignitionPending.filter((burn) => {
      const windowEnd = burn.start + this.config.IGNITION_WINDOW_S;
      if (now < windowEnd || (!force && this.hasPending(true, windowEnd))) return true;
      events.push(...this.checkIgnition(burn));
      return false;
    });
    return events;
  }

  private closeStep(): EngineEvent[] {
    const step = this.step;
    this.step = null;
    if (!step || this.isPlanned(step.after, step.burn) || this.isRingShutdown(step)) return [];
    const burn = step.burn;
    const relight = burn !== null && burn.index > 0
      && step.firstT - burn.start <= this.config.RELIGHT_GRACE_S;
    return [this.event(relight ? 'died_after_relight' : 'engine_out', step.offs, {
      metSeconds: step.firstT,
      metRange: step.range,
      runningAfter: step.after,
      expected: step.before,
    })];
  }

  /**
   * failed_to_light: in every ring where at least half the engines lit, the
   * engines still off at the end of the ignition window. A ring that stayed
   * dark as a whole is planned (e.g. the outer ring on boostback). This also
   * covers "planned count with only fully lit or fully dark rings": such a
   * burn has no partially lit ring, so nothing is reported.
   */
  private checkIgnition(burn: Burn): EngineEvent[] {
    if (burn.ignitionChecked) return [];
    burn.ignitionChecked = true;
    const liftoff = burn.atBaseline || (burn.index === 0 && burn.start <= this.config.LIFTOFF_MAX_MET_S);
    // Lit during a gap: the ignition was not seen, so an engine that is off
    // may as well have lit and died there
    if (liftoff || burn.startRange) return [];

    const failed: string[] = [];
    for (const ring of this.rings()) {
      const members = enginesInRing(this.vehicle, ring);
      const lit = members.filter((e) => burn.lit.has(e.id)).length;
      if (lit * 2 < members.length || lit === members.length) continue;
      for (const engine of members) {
        if (!burn.lit.has(engine.id) && this.state.get(engine.id) === false) failed.push(engine.id);
      }
    }
    if (failed.length === 0) return [];
    return [this.event('failed_to_light', failed, {
      metSeconds: burn.start,
      runningAfter: this.running,
      expected: burn.lit.size + failed.length,
    })];
  }

  private rings(): EngineRing[] {
    return [...new Set(enginesOf(this.vehicle).map((e) => e.ring))];
  }

  private event(
    kind: EngineEventKind,
    ids: string[],
    fields: Pick<EngineEvent, 'metSeconds' | 'metRange' | 'runningAfter' | 'expected'>,
  ): EngineEvent {
    const engineIds = [...ids].sort((a, b) => this.order.get(a)! - this.order.get(b)!);
    const byId = new Map(enginesOf(this.vehicle).map((e) => [e.id, e.ring]));
    const rings = [...new Set(engineIds.map((id) => byId.get(id)!))];
    const event: EngineEvent = {
      kind,
      vehicle: this.vehicle,
      engineIds,
      rings,
      metSeconds: fields.metSeconds,
      runningAfter: fields.runningAfter,
      expected: fields.expected,
    };
    if (fields.metRange) event.metRange = [...fields.metRange];
    return event;
  }
}

/**
 * Incremental engine event detector. Push records in sequence order; each
 * push returns the events it finalized (a shutdown step is only finalized
 * once its step window has passed, and frames with no engine active are held
 * up to FADE_MAX_S in case a panel gap follows, so events come a little after
 * the fact).
 */
export class EngineEventDetector {
  private trackers!: Record<Vehicle, VehicleTracker>;
  private all: EngineEvent[] = [];
  private lastT: number | null = null;
  private readonly config: EngineEventConfig;

  constructor(config: Partial<EngineEventConfig> = {}) {
    this.config = { ...ENGINE_EVENT_CONFIG, ...config };
    this.reset();
  }

  /**
   * Push one telemetry record. metSeconds defaults to the record's
   * whole-second MET; records without MET are ignored.
   */
  push(record: TelemetryRecord, metSeconds?: number): EngineEvent[] {
    let t = metSeconds;
    if (t === undefined) {
      if (!record.mission_elapsed_time) return [];
      t = parseMETToSeconds(record.mission_elapsed_time);
    }
    return this.pushStatuses(
      t,
      record.starship_engines,
      record.superheavy_engines,
      record.separation_state,
      shipAltitudeKm(record),
    );
  }

  /** Push several records; returns the events they finalized. */
  pushAll(records: Iterable<TelemetryRecord>, metSecondsOf?: (record: TelemetryRecord) => number | undefined): EngineEvent[] {
    const events: EngineEvent[] = [];
    for (const record of records) events.push(...this.push(record, metSecondsOf?.(record)));
    return events;
  }

  /**
   * Lower-level entry point: one frame's engine statuses at a MET in
   * seconds. Missing maps or engines read as 'undetected'. separationState is
   * informational (the ship altitude, in km, is only meaningful after
   * separation, so pass undefined otherwise).
   */
  pushStatuses(
    metSeconds: number,
    starship: EngineStatusMap | null | undefined,
    superheavy: EngineStatusMap | null | undefined,
    _separationState?: TelemetryRecord['separation_state'],
    shipAltitude?: number,
  ): EngineEvent[] {
    if (!Number.isFinite(metSeconds)) return [];
    this.lastT = metSeconds;
    const events = [
      ...this.trackers.superheavy.push(metSeconds, superheavy ?? {}),
      ...this.trackers.starship.push(metSeconds, starship ?? {}, shipAltitude),
    ];
    this.all.push(...events);
    return events;
  }

  /**
   * Finalize what the end of the data allows: open shutdown steps, and
   * ignition checks whose window has passed. Call once no more records follow.
   */
  flush(): EngineEvent[] {
    const events = [
      ...this.trackers.superheavy.flush(this.lastT),
      ...this.trackers.starship.flush(this.lastT),
    ];
    this.all.push(...events);
    return events;
  }

  /** Every event finalized so far, in finalization order. */
  events(): EngineEvent[] {
    return this.all;
  }

  reset(): void {
    this.trackers = {
      superheavy: new VehicleTracker('superheavy', this.config),
      starship: new VehicleTracker('starship', this.config),
    };
    this.all = [];
    this.lastT = null;
  }
}

/**
 * Detect the engine events of a complete record stream (flushed at the end).
 */
export function detectEngineEvents(
  records: Iterable<TelemetryRecord>,
  metSecondsOf?: (record: TelemetryRecord) => number | undefined,
  config?: Partial<EngineEventConfig>,
): EngineEvent[] {
  const detector = new EngineEventDetector(config);
  detector.pushAll(records, metSecondsOf);
  detector.flush();
  return detector.events();
}
