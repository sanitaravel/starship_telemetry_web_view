import { describe, it, expect } from 'vitest';
import type { TelemetryRecord } from '../../core/types';
import { isShipLandingBurn, shipAltitudeKm } from './landing';

/** A post-separation record with Starship on the left, reading `shipAlt` km there. */
function record(shipAlt: number): TelemetryRecord {
  const reading = (value: number | null) => ({
    value,
    unit: value === null ? null : 'KM',
    status: value === null ? 'not_found' : 'available',
  });
  return {
    sequence_number: 0,
    mission_elapsed_time: '+00:00:00',
    mission_elapsed_time_raw: null,
    speed_left: reading(null),
    speed_right: reading(null),
    altitude_left: reading(shipAlt),
    altitude_right: reading(null),
    stage_left_label: null,
    stage_right_label: null,
    stage_separation_text: null,
    stage_assignment_left: 'starship',
    stage_assignment_right: 'super_heavy',
    separation_state: 'post_separation',
    t_zero_found: true,
    stage_sep_found: true,
    starship_engines: {},
    superheavy_engines: {},
    detection_accuracy: { starship: 1, superheavy: 1 },
    timestamp: 0,
  };
}

describe('ship altitude and landing predicate', () => {
  it('reads the Starship side after separation only', () => {
    const r = record(12);
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
