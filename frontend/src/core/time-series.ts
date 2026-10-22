/**
 * A single data point in the time-series store.
 */
export interface TimeSeriesPoint {
  missionElapsedTime: string;
  /**
   * MET in seconds from T-0 with a sub-second offset (see MetClock). When
   * absent, consumers fall back to the whole-second missionElapsedTime.
   */
  metSeconds?: number;
  timestamp: number;
  value: number;
  unit: string;
}

/**
 * Time-series data store for speed/altitude graphs per vehicle.
 */
export interface TimeSeriesStore {
  speedSuperHeavy: TimeSeriesPoint[];
  speedStarship: TimeSeriesPoint[];
  altitudeSuperHeavy: TimeSeriesPoint[];
  altitudeStarship: TimeSeriesPoint[];
}
