import type { TimeSeriesStore } from '../core/time-series';
import type { PreviousFlightColumns, PreviousFlightInfo } from '../core/types';
import type { EngineEvent } from '../domain/engines/events';
import {
  detectFlightEngineEvents,
  estimateMetSeconds,
  estimateSeparationMet,
  transformToTimeSeries,
} from '../domain/flights';

/** A loaded previous flight: chart series and the engine events detected in it. */
export interface PreviousFlightData {
  store: TimeSeriesStore;
  engineEvents: EngineEvent[];
  /** Estimated MET of stage separation in seconds, or null if unknown (see estimateSeparationMet). */
  separationMetSeconds: number | null;
}

/**
 * Fetch the list of available previous flights from the backend.
 */
export async function fetchPreviousFlightList(): Promise<PreviousFlightInfo[]> {
  const response = await fetch('/api/previous-flights');
  if (!response.ok) {
    return [];
  }
  return response.json();
}

/**
 * Fetch a specific previous flight: its TimeSeriesStore and engine events.
 */
export async function fetchPreviousFlightData(filename: string): Promise<PreviousFlightData | null> {
  const response = await fetch(`/api/previous-flights/${encodeURIComponent(filename)}`);
  if (!response.ok) {
    return null;
  }
  const columns: PreviousFlightColumns = await response.json();
  const metSeconds = estimateMetSeconds(columns);
  return {
    store: transformToTimeSeries(columns, metSeconds),
    engineEvents: detectFlightEngineEvents(columns, metSeconds),
    separationMetSeconds: estimateSeparationMet(columns, metSeconds),
  };
}
