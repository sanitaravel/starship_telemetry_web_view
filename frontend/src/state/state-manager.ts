import type { TelemetryRecord, PipelineStatus, ErrorPayload, ValidationResult, FramePayload, ReplayPayload, WebSocketMessage } from '../core/types';
import { FPSMeter } from '../core/fps-meter';
import { createLogger } from '../core/logger';
import { MetClock } from '../core/met-clock';
import type { TimeSeriesStore } from '../core/time-series';
import { parseMETToSeconds } from '../core/met';
import { EngineEventDetector } from '../domain/engines/detector';
import type { EngineEvent } from '../domain/engines/events';
import { appendToTimeSeriesInPlace, createEmptyTimeSeries } from './time-series-store';

const logger = createLogger('state');

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
  /**
   * MET in seconds (same estimate as metSeconds) of the first record after
   * stage separation, or null until one arrives.
   */
  separationMetSeconds: number | null;
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
  /** Run id of the last replay chunk 0, to spot a later chunk from another Run */
  private replayRunId: string | null = null;

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
      case 'replay':
        this.handleReplay(message.payload as ReplayPayload);
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
    const { appended, newEvents } = this.ingest(record);
    this.state = {
      ...this.state,
      latestTelemetry: record,
      timeSeriesVersion: this.state.timeSeriesVersion + (appended ? 1 : 0),
      engineEventsVersion: this.state.engineEventsVersion + (newEvents ? 1 : 0),
      processingFps: this.fpsMeter.getFps(),
    };
    logger.debug('Telemetry update applied', { sequence_number: record.sequence_number });
    this.notifySubscribers();
  }

  /**
   * Ingest one chunk of the server's replay of the current Run. Chunk 0
   * starts a replay (a fresh connection or a new Run), so it first drops the
   * live data received so far; the records then go through the same
   * per-record path as live telemetry, without FPS ticks, and subscribers
   * are notified once for the whole chunk.
   */
  private handleReplay(replay: ReplayPayload): void {
    if (replay.chunk === 0) {
      this.resetLiveData();
      this.replayRunId = replay.run_id;
    } else if (replay.run_id !== this.replayRunId) {
      logger.warn('Replay chunk from a different run than its chunk 0', {
        chunk: replay.chunk,
        run_id: replay.run_id,
        expected: this.replayRunId,
      });
    }
    let appendedAny = false;
    let newEventsAny = false;
    for (const record of replay.records) {
      const { appended, newEvents } = this.ingest(record);
      appendedAny = appendedAny || appended;
      newEventsAny = newEventsAny || newEvents;
    }
    const last = replay.records[replay.records.length - 1];
    this.state = {
      ...this.state,
      latestTelemetry: last ?? this.state.latestTelemetry,
      timeSeriesVersion: this.state.timeSeriesVersion + (appendedAny ? 1 : 0),
      engineEventsVersion: this.state.engineEventsVersion + (newEventsAny ? 1 : 0),
    };
    logger.debug('Replay chunk applied', {
      chunk: replay.chunk,
      records: replay.records.length,
      final: replay.final,
      truncated: replay.truncated,
    });
    this.notifySubscribers();
  }

  /**
   * The per-record part of live telemetry: MET estimate, time series, kept
   * records, engine events and the separation MET. The containers grow in
   * place; the caller builds the rest of the new state and notifies.
   */
  private ingest(record: TelemetryRecord): { appended: boolean; newEvents: boolean } {
    const met = record.mission_elapsed_time;
    const metSeconds = met
      ? this.metClock.estimate(parseMETToSeconds(met), record.timestamp)
      : undefined;
    const appended = appendToTimeSeriesInPlace(this.state.timeSeries, record, metSeconds);
    this.state.telemetryRecords.push(record);
    const engineEvents = this.engineEventDetector.push(record, metSeconds);
    this.state.engineEvents.push(...engineEvents);
    if (
      this.state.separationMetSeconds === null &&
      record.separation_state === 'post_separation' &&
      metSeconds !== undefined
    ) {
      this.state = { ...this.state, separationMetSeconds: metSeconds };
    }
    return { appended, newEvents: engineEvents.length > 0 };
  }

  /**
   * Drop the live data (fresh containers, so subscribers comparing identity
   * redraw) and the clock and detector built from it. Keeps the pipeline
   * status, latest error, validation result and frame, and the FPS meter.
   */
  private resetLiveData(): void {
    this.metClock.reset();
    this.engineEventDetector.reset();
    const initial = createInitialState();
    this.state = {
      ...this.state,
      latestTelemetry: null,
      timeSeries: initial.timeSeries,
      // Bumped rather than zeroed, so a subscriber comparing versions redraws
      timeSeriesVersion: this.state.timeSeriesVersion + 1,
      telemetryRecords: initial.telemetryRecords,
      engineEvents: initial.engineEvents,
      engineEventsVersion: this.state.engineEventsVersion + 1,
      separationMetSeconds: null,
    };
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
    timeSeries: createEmptyTimeSeries(),
    timeSeriesVersion: 0,
    telemetryRecords: [],
    engineEvents: [],
    engineEventsVersion: 0,
    separationMetSeconds: null,
    processingFps: 0,
  };
}
