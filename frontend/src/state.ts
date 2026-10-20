import type { TelemetryRecord, PipelineStatus, ErrorPayload, ValidationResult, FramePayload, WebSocketMessage } from './types';
import { FPSMeter } from './fps-meter';
import { createLogger } from './logger';
import { MetClock } from './met-clock';
import { parseMETToSeconds } from './time-series-graphs/series-math';
import { EngineEventDetector, type EngineEvent } from './engine-events';

const logger = createLogger('state');

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

/**
 * Complete application state.
 */
export interface AppState {
  pipelineStatus: PipelineStatus | null;
  latestTelemetry: TelemetryRecord | null;
  latestError: ErrorPayload | null;
  latestValidationResult: ValidationResult | null;
  latestFrame: FramePayload | null;
  /**
   * Live time series. The arrays grow in place as telemetry arrives, so
   * compare timeSeriesVersion (not identity) to detect new points.
   */
  timeSeries: TimeSeriesStore;
  /** Incremented whenever a point is appended to timeSeries */
  timeSeriesVersion: number;
  /**
   * Every telemetry record received, in arrival order, kept for download.
   * Grows in place like timeSeries.
   */
  telemetryRecords: TelemetryRecord[];
  /**
   * Engine failures detected in the live telemetry, in detection order.
   * Grows in place like timeSeries; compare engineEventsVersion to detect new ones.
   */
  engineEvents: EngineEvent[];
  /** Incremented whenever events are appended to engineEvents */
  engineEventsVersion: number;
  /** Frames processed per second, measured from actual message arrivals */
  processingFps: number;
}

export type StateChangeHandler = (state: AppState) => void;

/**
 * Application state manager.
 * Receives WebSocket messages and maintains the frontend state,
 * notifying subscribers of changes.
 */
export class StateManager {
  private state: AppState;
  private subscribers: StateChangeHandler[] = [];
  private fpsMeter: FPSMeter = new FPSMeter();
  private metClock: MetClock = new MetClock();
  private engineEventDetector: EngineEventDetector = new EngineEventDetector();

  constructor() {
    this.state = createInitialState();
    logger.debug('StateManager initialized');
  }

  /**
   * Get the current application state.
   */
  getState(): AppState {
    return this.state;
  }

  /**
   * Subscribe to state changes. Returns an unsubscribe function.
   */
  subscribe(handler: StateChangeHandler): () => void {
    this.subscribers.push(handler);
    return () => {
      this.subscribers = this.subscribers.filter((h) => h !== handler);
    };
  }

  /**
   * Handle an incoming WebSocket message and update state accordingly.
   */
  handleMessage(message: WebSocketMessage): void {
    switch (message.type) {
      case 'telemetry':
        this.handleTelemetry(message.payload as TelemetryRecord);
        break;
      case 'status':
        this.handleStatus(message.payload as PipelineStatus);
        break;
      case 'error':
        this.handleError(message.payload as ErrorPayload);
        break;
      case 'validation_result':
        this.handleValidationResult(message.payload as ValidationResult);
        break;
      case 'frame':
        this.handleFrame(message.payload as FramePayload);
        break;
    }
  }

  /**
   * Reset state to initial values.
   */
  reset(): void {
    this.fpsMeter.reset();
    this.metClock.reset();
    this.engineEventDetector.reset();
    this.state = createInitialState();
    this.notifySubscribers();
  }

  /**
   * Log a rendering error for a component.
   */
  logRenderError(componentName: string, error: Error): void {
    logger.error('Rendering error', {
      component: componentName,
      message: error.message,
      stack: error.stack ?? null,
    });
  }

  private handleTelemetry(record: TelemetryRecord): void {
    this.fpsMeter.tick();
    const met = record.mission_elapsed_time;
    const metSeconds = met
      ? this.metClock.estimate(parseMETToSeconds(met), record.timestamp)
      : undefined;
    const appended = appendToTimeSeriesInPlace(this.state.timeSeries, record, metSeconds);
    this.state.telemetryRecords.push(record);
    const engineEvents = this.engineEventDetector.push(record, metSeconds);
    this.state.engineEvents.push(...engineEvents);
    this.state = {
      ...this.state,
      latestTelemetry: record,
      timeSeriesVersion: this.state.timeSeriesVersion + (appended ? 1 : 0),
      engineEventsVersion: this.state.engineEventsVersion + (engineEvents.length > 0 ? 1 : 0),
      processingFps: this.fpsMeter.getFps(),
    };
    logger.debug('Telemetry update applied', { sequence_number: record.sequence_number });
    this.notifySubscribers();
  }

  private handleStatus(status: PipelineStatus): void {
    const previous = this.state.pipelineStatus?.status ?? 'none';
    const next = status.status;
    if (previous !== next) {
      logger.info(`Pipeline status: ${previous} → ${next}`);
    }
    // Reset FPS meter when pipeline stops or disconnects
    if (status.status === 'stopped' || status.status === 'disconnected') {
      this.fpsMeter.reset();
    }
    this.state = {
      ...this.state,
      pipelineStatus: status,
      processingFps: this.fpsMeter.getFps(),
    };
    this.notifySubscribers();
  }

  private handleError(error: ErrorPayload): void {
    logger.error('WebSocket error received', { code: error.code, message: error.message });
    this.state = {
      ...this.state,
      latestError: error,
    };
    this.notifySubscribers();
  }

  private handleValidationResult(result: ValidationResult): void {
    this.state = {
      ...this.state,
      latestValidationResult: result,
    };
    this.notifySubscribers();
  }

  private handleFrame(frame: FramePayload): void {
    this.state = {
      ...this.state,
      latestFrame: frame,
    };
    this.notifySubscribers();
  }

  private notifySubscribers(): void {
    for (const subscriber of this.subscribers) {
      subscriber(this.state);
    }
  }
}

/**
 * Create the initial application state.
 */
export function createInitialState(): AppState {
  return {
    pipelineStatus: null,
    latestTelemetry: null,
    latestError: null,
    latestValidationResult: null,
    latestFrame: null,
    timeSeries: {
      speedSuperHeavy: [],
      speedStarship: [],
      altitudeSuperHeavy: [],
      altitudeStarship: [],
    },
    timeSeriesVersion: 0,
    telemetryRecords: [],
    engineEvents: [],
    engineEventsVersion: 0,
    processingFps: 0,
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
