import type { TelemetryRecord } from '../core/types';
import type { ShipTrack } from '../core/ship-track';

/**
 * Exports of what the dashboard has collected this session, in the formats
 * the repo already stores: OCR telemetry as a `results_previous/` flight file
 * (or the raw WebSocket records), and a live ship track as a
 * `trajectories/positions_ship<N>.csv` log.
 */

type EngineStatuses = TelemetryRecord['starship_engines'];

/** One vehicle's reading in a flight file. Speed is km/h, altitude km. */
export interface FlightVehicle {
  speed: number | null;
  altitude: number | null;
  engines: EngineStatuses;
}

/** One record of a `results_previous/` flight file. */
export interface FlightRecord {
  frame_number: number | null;
  vehicles: { starship: FlightVehicle; superheavy: FlightVehicle };
  /** Empty when the frame had no readable MET (the flight loader skips those). */
  time: { sign: '+' | '-'; hours: number; minutes: number; seconds: number } | Record<string, never>;
  /** Offset of the frame in the source video. */
  real_time_seconds: number;
  real_time: { hours: number; minutes: number; seconds: number; milliseconds: number };
}

const MET_PATTERN = /^T?([+-])?(\d+):(\d{1,2}):(\d{1,2})$/;

/** Factors to the flight file's units (km/h for speed, km for altitude). */
const TO_METRIC: Record<string, number> = {
  'KM/H': 1,
  MPH: 1.609344,
  KM: 1,
  MI: 1.609344,
  FT: 0.0003048,
};

type Reading = TelemetryRecord['speed_left'];

/** The reading in metric units, or null when it was not read or the unit is unknown. */
function metricValue(reading: Reading): number | null {
  if (reading.status !== 'available' || reading.value === null) return null;
  // A missing unit means it was not read this frame; the HUD defaults to metric.
  const factor = TO_METRIC[(reading.unit ?? 'KM').toUpperCase()];
  if (factor === undefined) return null;
  return factor === 1 ? reading.value : Math.round(reading.value * factor * 1000) / 1000;
}

function parseMet(met: string | null): FlightRecord['time'] {
  const match = met ? MET_PATTERN.exec(met.trim()) : null;
  if (!match) return {};
  return {
    sign: match[1] === '-' ? '-' : '+',
    hours: Number(match[2]),
    minutes: Number(match[3]),
    seconds: Number(match[4]),
  };
}

function splitSeconds(totalSeconds: number): FlightRecord['real_time'] {
  const ms = Math.round(totalSeconds * 1000);
  return {
    hours: Math.floor(ms / 3_600_000),
    minutes: Math.floor(ms / 60_000) % 60,
    seconds: Math.floor(ms / 1000) % 60,
    milliseconds: ms % 1000,
  };
}

type Side = 'left' | 'right';
type Vehicle = 'starship' | 'super_heavy';

function sideReading(record: TelemetryRecord, quantity: 'speed' | 'altitude', side: Side): number | null {
  return metricValue(record[`${quantity}_${side}`]);
}

/**
 * A vehicle's speed or altitude. Before separation the HUD shows the stack,
 * so both vehicles get the same reading from whichever side has one (the
 * recorded flights store it this way, and the graphs plot it this way).
 * After separation, the reading comes from the side assigned to the vehicle.
 */
function vehicleReading(record: TelemetryRecord, quantity: 'speed' | 'altitude', vehicle: Vehicle): number | null {
  const sides: Side[] =
    record.separation_state === 'pre_separation'
      ? ['left', 'right']
      : (['left', 'right'] as const).filter((side) => record[`stage_assignment_${side}`] === vehicle);
  for (const side of sides) {
    const value = sideReading(record, quantity, side);
    if (value !== null) return value;
  }
  return null;
}

function vehicle(record: TelemetryRecord, name: Vehicle, engines: EngineStatuses): FlightVehicle {
  return {
    speed: vehicleReading(record, 'speed', name),
    altitude: vehicleReading(record, 'altitude', name),
    engines: { ...engines },
  };
}

/** Converts received records to the `results_previous/` format, in video order. */
export function toFlightRecords(records: readonly TelemetryRecord[]): FlightRecord[] {
  return [...records]
    .sort((a, b) => a.timestamp - b.timestamp || a.sequence_number - b.sequence_number)
    .map((record) => {
      const realTimeSeconds = record.timestamp / 1000;
      return {
        frame_number: record.frame_number ?? null,
        vehicles: {
          starship: vehicle(record, 'starship', record.starship_engines),
          superheavy: vehicle(record, 'super_heavy', record.superheavy_engines),
        },
        time: parseMet(record.mission_elapsed_time),
        real_time_seconds: realTimeSeconds,
        real_time: splitSeconds(realTimeSeconds),
      };
    });
}

export const TRAJECTORY_CSV_COLUMNS = [
  'polled_at',
  'ship',
  'gps_time',
  'mission_time',
  'latitude',
  'longitude',
  'altitude',
  'speed',
] as const;

/** A ship track in the `trajectories/positions_ship<N>.csv` format. */
export function trackToCsv(track: ShipTrack): string {
  const rows = track.positions.map((p) =>
    [
      p.polledAt === undefined ? '' : new Date(p.polledAt).toISOString().replace('Z', '+00:00'),
      track.id,
      p.gpsTime,
      p.missionTime,
      p.latitude,
      p.longitude,
      p.altitude,
      p.speed,
    ].join(','),
  );
  return [TRAJECTORY_CSV_COLUMNS.join(','), ...rows].join('\n') + '\n';
}

/** Name under which `trajectories/` picks the track up, e.g. `positions_ship40.csv`. */
export function trackFilename(track: ShipTrack): string {
  return `positions_${track.id}.csv`;
}

/** e.g. `telemetry-2026-10-19T14-05-09.json` (UTC), with an optional suffix before the extension. */
export function telemetryFilename(now: Date = new Date(), suffix = ''): string {
  const stamp = now.toISOString().slice(0, 19).replace(/:/g, '-');
  return `telemetry-${stamp}${suffix}.json`;
}

/** Saves text as a file download. */
export function downloadText(filename: string, content: string, type: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser a tick to start the download before releasing the URL
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
