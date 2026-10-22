/**
 * Engine event and engine status shapes shared by the detector and its
 * consumers.
 */

import type { Vehicle } from './layout';

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
