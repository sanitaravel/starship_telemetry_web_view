/**
 * Ship landing burn recognition: the ship's altitude from a record, and
 * whether a burn is the landing burn.
 */

import type { TelemetryRecord } from '../../core/types';
import { ENGINE_EVENT_CONFIG, type EngineEventConfig } from './config';

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
