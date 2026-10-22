import type { TelemetryRecord } from '../core/types';
import type { TimeSeriesPoint, TimeSeriesStore } from '../core/time-series';

/**
 * Create an empty time-series store.
 */
export function createEmptyTimeSeries(): TimeSeriesStore {
  return {
    speedSuperHeavy: [],
    speedStarship: [],
    altitudeSuperHeavy: [],
    altitudeStarship: [],
  };
}

/**
 * Append telemetry data to the time-series store based on stage assignment.
 * Only appends points with non-null values and a valid mission elapsed time.
 * metSeconds, when given, is the record's sub-second MET estimate.
 */
export function appendToTimeSeries(
  store: TimeSeriesStore,
  record: TelemetryRecord,
  metSeconds?: number
): TimeSeriesStore {
  if (!record.mission_elapsed_time) {
    return store;
  }

  const newStore: TimeSeriesStore = {
    speedSuperHeavy: [...store.speedSuperHeavy],
    speedStarship: [...store.speedStarship],
    altitudeSuperHeavy: [...store.altitudeSuperHeavy],
    altitudeStarship: [...store.altitudeStarship],
  };
  appendToTimeSeriesInPlace(newStore, record, metSeconds);
  return newStore;
}

/**
 * Append telemetry data to the store's arrays in place (same rules as
 * appendToTimeSeries). Copying every series on each record made a session
 * O(n²); appending keeps it O(1) per record.
 *
 * @returns true if at least one point was appended.
 */
export function appendToTimeSeriesInPlace(
  newStore: TimeSeriesStore,
  record: TelemetryRecord,
  metSeconds?: number
): boolean {
  const met = record.mission_elapsed_time;
  if (!met) {
    return false;
  }
  const before =
    newStore.speedSuperHeavy.length +
    newStore.speedStarship.length +
    newStore.altitudeSuperHeavy.length +
    newStore.altitudeStarship.length;

  // Left side
  if (record.speed_left.value !== null && record.speed_left.status === 'available') {
    const point: TimeSeriesPoint = {
      missionElapsedTime: met,
      metSeconds,
      timestamp: record.timestamp,
      value: record.speed_left.value,
      unit: record.speed_left.unit ?? '',
    };
    if (record.separation_state === 'pre_separation') {
      // Pre-separation: data goes to both vehicles
      newStore.speedSuperHeavy.push(point);
      newStore.speedStarship.push({ ...point });
    } else if (record.stage_assignment_left === 'super_heavy') {
      newStore.speedSuperHeavy.push(point);
    } else {
      newStore.speedStarship.push(point);
    }
  }

  if (record.altitude_left.value !== null && record.altitude_left.status === 'available') {
    const point: TimeSeriesPoint = {
      missionElapsedTime: met,
      metSeconds,
      timestamp: record.timestamp,
      value: record.altitude_left.value,
      unit: record.altitude_left.unit ?? '',
    };
    if (record.separation_state === 'pre_separation') {
      newStore.altitudeSuperHeavy.push(point);
      newStore.altitudeStarship.push({ ...point });
    } else if (record.stage_assignment_left === 'super_heavy') {
      newStore.altitudeSuperHeavy.push(point);
    } else {
      newStore.altitudeStarship.push(point);
    }
  }

  // Right side
  if (record.speed_right.value !== null && record.speed_right.status === 'available') {
    const point: TimeSeriesPoint = {
      missionElapsedTime: met,
      metSeconds,
      timestamp: record.timestamp,
      value: record.speed_right.value,
      unit: record.speed_right.unit ?? '',
    };
    if (record.separation_state === 'pre_separation') {
      newStore.speedSuperHeavy.push(point);
      newStore.speedStarship.push({ ...point });
    } else if (record.stage_assignment_right === 'super_heavy') {
      newStore.speedSuperHeavy.push(point);
    } else {
      newStore.speedStarship.push(point);
    }
  }

  if (record.altitude_right.value !== null && record.altitude_right.status === 'available') {
    const point: TimeSeriesPoint = {
      missionElapsedTime: met,
      metSeconds,
      timestamp: record.timestamp,
      value: record.altitude_right.value,
      unit: record.altitude_right.unit ?? '',
    };
    if (record.separation_state === 'pre_separation') {
      newStore.altitudeSuperHeavy.push(point);
      newStore.altitudeStarship.push({ ...point });
    } else if (record.stage_assignment_right === 'super_heavy') {
      newStore.altitudeSuperHeavy.push(point);
    } else {
      newStore.altitudeStarship.push(point);
    }
  }

  const after =
    newStore.speedSuperHeavy.length +
    newStore.speedStarship.length +
    newStore.altitudeSuperHeavy.length +
    newStore.altitudeStarship.length;
  return after > before;
}
