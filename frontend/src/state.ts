import type { TelemetryRecord, PipelineStatus, ErrorPayload, ValidationResult, FramePayload, WebSocketMessage } from './types';
import { FPSMeter } from './fps-meter';

/**
 * A single data point in the time-series store.
 */
export interface TimeSeriesPoint {
  missionElapsedTime: string;
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
  timeSeries: TimeSeriesStore;
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

  constructor() {
    this.state = createInitialState();
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
    this.state = createInitialState();
    this.notifySubscribers();
  }

  private handleTelemetry(record: TelemetryRecord): void {
    this.fpsMeter.tick();
    this.state = {
      ...this.state,
      latestTelemetry: record,
      timeSeries: appendToTimeSeries(this.state.timeSeries, record),
      processingFps: this.fpsMeter.getFps(),
    };
    this.notifySubscribers();
  }

  private handleStatus(status: PipelineStatus): void {
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
    processingFps: 0,
  };
}

/**
 * Append telemetry data to the time-series store based on stage assignment.
 * Only appends points with non-null values and a valid mission elapsed time.
 */
export function appendToTimeSeries(
  store: TimeSeriesStore,
  record: TelemetryRecord
): TimeSeriesStore {
  const met = record.mission_elapsed_time;
  if (!met) {
    return store;
  }

  const newStore: TimeSeriesStore = {
    speedSuperHeavy: [...store.speedSuperHeavy],
    speedStarship: [...store.speedStarship],
    altitudeSuperHeavy: [...store.altitudeSuperHeavy],
    altitudeStarship: [...store.altitudeStarship],
  };

  // Left side
  if (record.speed_left.value !== null && record.speed_left.status === 'available') {
    const point: TimeSeriesPoint = {
      missionElapsedTime: met,
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

  return newStore;
}
