import { describe, it, expect } from 'vitest';
import { telemetryFilename, toFlightRecords, trackFilename, trackToCsv } from './downloads';
import type { TelemetryRecord } from '../core/types';
import type { ShipTrack } from '../core/ship-track';

function record(overrides: Partial<TelemetryRecord> = {}): TelemetryRecord {
  return {
    sequence_number: 1,
    frame_number: 2700,
    mission_elapsed_time: 'T+00:01:30',
    mission_elapsed_time_raw: '00:01:30',
    speed_left: { value: 1200, unit: 'KM/H', status: 'available' },
    speed_right: { value: 1300, unit: 'KM/H', status: 'available' },
    altitude_left: { value: 45.2, unit: 'KM', status: 'available' },
    altitude_right: { value: 50.1, unit: 'KM', status: 'available' },
    stage_left_label: 'SUPER HEAVY',
    stage_right_label: 'STARSHIP',
    stage_separation_text: null,
    stage_assignment_left: 'super_heavy',
    stage_assignment_right: 'starship',
    separation_state: 'post_separation',
    t_zero_found: true,
    stage_sep_found: true,
    starship_engines: { ss_e1: 'active' },
    superheavy_engines: { sh_e1: 'inactive' },
    detection_accuracy: { starship: 1, superheavy: 1 },
    timestamp: 90_500,
    ...overrides,
  };
}

describe('toFlightRecords', () => {
  it('writes the results_previous record shape', () => {
    const [flight] = toFlightRecords([record()]);
    expect(flight).toEqual({
      frame_number: 2700,
      vehicles: {
        starship: { speed: 1300, altitude: 50.1, engines: { ss_e1: 'active' } },
        superheavy: { speed: 1200, altitude: 45.2, engines: { sh_e1: 'inactive' } },
      },
      time: { sign: '+', hours: 0, minutes: 1, seconds: 30 },
      real_time_seconds: 90.5,
      real_time: { hours: 0, minutes: 1, seconds: 30, milliseconds: 500 },
    });
  });

  it('follows the stage assignment when the sides are swapped', () => {
    const [flight] = toFlightRecords([
      record({ stage_assignment_left: 'starship', stage_assignment_right: 'super_heavy' }),
    ]);
    expect(flight.vehicles.starship.speed).toBe(1200);
    expect(flight.vehicles.superheavy.speed).toBe(1300);
  });

  it('gives the stack reading to both vehicles before separation', () => {
    const preSeparation = {
      separation_state: 'pre_separation',
      stage_assignment_left: 'super_heavy',
      stage_assignment_right: 'super_heavy',
    } as const;
    const [onlyRight, bothSides] = toFlightRecords([
      record({
        ...preSeparation,
        speed_left: { value: null, unit: null, status: 'unavailable' },
        altitude_left: { value: null, unit: null, status: 'unavailable' },
      }),
      record({ ...preSeparation, timestamp: 91_000 }),
    ]);
    expect(onlyRight.vehicles.superheavy).toMatchObject({ speed: 1300, altitude: 50.1 });
    expect(onlyRight.vehicles.starship).toMatchObject({ speed: 1300, altitude: 50.1 });
    expect(bothSides.vehicles.superheavy.speed).toBe(bothSides.vehicles.starship.speed);
  });

  it('after separation, falls back to the other side assigned to the same vehicle', () => {
    const [flight] = toFlightRecords([
      record({
        stage_assignment_left: 'starship',
        stage_assignment_right: 'starship',
        speed_left: { value: null, unit: null, status: 'unavailable' },
      }),
    ]);
    expect(flight.vehicles.starship.speed).toBe(1300);
    expect(flight.vehicles.superheavy).toMatchObject({ speed: null, altitude: null });
  });

  it('nulls unread values and converts imperial units to metric', () => {
    const [flight] = toFlightRecords([
      record({
        speed_left: { value: null, unit: null, status: 'unavailable' },
        altitude_left: { value: 99, unit: 'KM', status: 'occluded_by_engines' },
        speed_right: { value: 1000, unit: 'MPH', status: 'available' },
        altitude_right: { value: 10_000, unit: 'FT', status: 'available' },
      }),
    ]);
    expect(flight.vehicles.superheavy).toMatchObject({ speed: null, altitude: null });
    expect(flight.vehicles.starship).toMatchObject({ speed: 1609.344, altitude: 3.048 });
  });

  it('leaves time empty without a readable MET and frame_number null on old backends', () => {
    const [flight] = toFlightRecords([record({ mission_elapsed_time: null, frame_number: undefined })]);
    expect(flight.time).toEqual({});
    expect(flight.frame_number).toBeNull();
  });

  it('parses a negative MET', () => {
    const [flight] = toFlightRecords([record({ mission_elapsed_time: 'T-00:00:05' })]);
    expect(flight.time).toEqual({ sign: '-', hours: 0, minutes: 0, seconds: 5 });
  });

  it('orders records by video position', () => {
    const flights = toFlightRecords([
      record({ sequence_number: 2, timestamp: 2000 }),
      record({ sequence_number: 1, timestamp: 1000 }),
    ]);
    expect(flights.map((f) => f.real_time_seconds)).toEqual([1, 2]);
  });
});

describe('trackToCsv', () => {
  const track: ShipTrack = {
    id: 'ship40',
    number: 40,
    positions: [
      {
        gpsTime: 1463524207.004812,
        missionTime: 107.485684,
        latitude: 25.996841,
        longitude: -97.158016,
        altitude: 96.574026,
        speed: 0,
        polledAt: Date.UTC(2026, 4, 22, 22, 29, 58, 194),
      },
      { gpsTime: 1463524217, missionTime: 117.5, latitude: 26, longitude: -97, altitude: 100, speed: 5 },
    ],
  };

  it('writes the trajectories/ CSV columns, one row per sample', () => {
    expect(trackToCsv(track)).toBe(
      'polled_at,ship,gps_time,mission_time,latitude,longitude,altitude,speed\n' +
        '2026-05-22T22:29:58.194+00:00,ship40,1463524207.004812,107.485684,25.996841,-97.158016,96.574026,0\n' +
        ',ship40,1463524217,117.5,26,-97,100,5\n',
    );
  });

  it('names the file so trajectories/ picks it up', () => {
    expect(trackFilename(track)).toBe('positions_ship40.csv');
  });
});

describe('telemetryFilename', () => {
  it('stamps the UTC time without colons', () => {
    const now = new Date(Date.UTC(2026, 9, 19, 14, 5, 9));
    expect(telemetryFilename(now)).toBe('telemetry-2026-10-19T14-05-09.json');
    expect(telemetryFilename(now, '-raw')).toBe('telemetry-2026-10-19T14-05-09-raw.json');
  });
});
