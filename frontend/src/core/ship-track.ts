/** One `current` sample from the feed. Altitude is metres, speed is m/s. */
export interface ShipPosition {
  gpsTime: number;
  missionTime: number;
  altitude: number;
  speed: number;
  latitude: number;
  longitude: number;
  /** Unix ms of the poll that first returned this sample; set when stored in a track. */
  polledAt?: number;
}

export interface ShipTrack {
  /** Feed key, e.g. `ship40`. */
  id: string;
  /** Vehicle number parsed from the key, e.g. 40. */
  number: number;
  /** Samples in gps_time order; only those with altitude >= 0 are kept. */
  positions: ShipPosition[];
}
