/**
 * Engine failure detection from per-frame engine statuses.
 *
 * Pure logic, no DOM: feed records in sequence order to an
 * EngineEventDetector and it reports engines that shut down unexpectedly
 * (engine_out), died shortly after a relight (died_after_relight) or did not
 * light with the rest of their ring (failed_to_light). Each vehicle is
 * tracked independently by a VehicleTracker (see vehicle-tracker.ts for the
 * rules).
 */

import type { TelemetryRecord } from '../../core/types';
import { parseMETToSeconds } from '../../core/met';
import type { Vehicle } from './layout';
import type { EngineEvent, EngineStatusMap } from './events';
import { ENGINE_EVENT_CONFIG, type EngineEventConfig } from './config';
import { shipAltitudeKm } from './landing';
import { VehicleTracker } from './vehicle-tracker';

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
