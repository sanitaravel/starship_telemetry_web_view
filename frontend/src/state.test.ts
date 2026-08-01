import { describe, it, expect } from 'vitest';
import { StateManager, appendToTimeSeries, createInitialState } from './state';
import type { TelemetryRecord, PipelineStatus, ErrorPayload, WebSocketMessage } from './types';

function createTestTelemetryRecord(overrides: Partial<TelemetryRecord> = {}): TelemetryRecord {
  return {
    sequence_number: 1,
    mission_elapsed_time: 'T+00:01:30',
    mission_elapsed_time_raw: '00:01:30',
    speed_left: { value: 1200.5, unit: 'KM/H', status: 'available' },
    speed_right: { value: 1300.0, unit: 'KM/H', status: 'available' },
    altitude_left: { value: 45.2, unit: 'KM', status: 'available' },
    altitude_right: { value: 50.1, unit: 'KM', status: 'available' },
    stage_left_label: 'SUPER HEAVY',
    stage_right_label: 'STARSHIP',
    stage_separation_text: 'STAGE SEP',
    stage_assignment_left: 'super_heavy',
    stage_assignment_right: 'starship',
    separation_state: 'post_separation',
    t_zero_found: true,
    stage_sep_found: true,
    starship_engines: { ss_e1: 'active', ss_e2: 'active', ss_e3: 'inactive', ss_e4: 'active', ss_e5: 'active', ss_e6: 'undetected' },
    superheavy_engines: Object.fromEntries(
      Array.from({ length: 33 }, (_, i) => [`sh_e${i + 1}`, 'inactive' as const])
    ),
    detection_accuracy: { starship: 0.83, superheavy: 0.97 },
    timestamp: 1700000000000,
    ...overrides,
  };
}

describe('StateManager', () => {
  it('initializes with null state', () => {
    const manager = new StateManager();
    const state = manager.getState();
    expect(state.pipelineStatus).toBeNull();
    expect(state.latestTelemetry).toBeNull();
    expect(state.latestError).toBeNull();
    expect(state.timeSeries.speedSuperHeavy).toHaveLength(0);
    expect(state.timeSeries.speedStarship).toHaveLength(0);
    expect(state.timeSeries.altitudeSuperHeavy).toHaveLength(0);
    expect(state.timeSeries.altitudeStarship).toHaveLength(0);
  });

  it('updates latestTelemetry on telemetry message', () => {
    const manager = new StateManager();
    const record = createTestTelemetryRecord();

    manager.handleMessage({ type: 'telemetry', payload: record });

    expect(manager.getState().latestTelemetry).toEqual(record);
  });

  it('updates pipelineStatus on status message', () => {
    const manager = new StateManager();
    const status: PipelineStatus = {
      status: 'running',
      gpu: { available: true, device_name: 'NVIDIA RTX 4090' },
      skip_frames: 30,
      current_sequence: 42,
    };

    manager.handleMessage({ type: 'status', payload: status });

    expect(manager.getState().pipelineStatus).toEqual(status);
  });

  it('updates latestError on error message', () => {
    const manager = new StateManager();
    const error: ErrorPayload = { code: 'STREAM_LOST', message: 'Connection dropped' };

    manager.handleMessage({ type: 'error', payload: error });

    expect(manager.getState().latestError).toEqual(error);
  });

  it('notifies subscribers on state change', () => {
    const manager = new StateManager();
    const states: any[] = [];
    manager.subscribe((state) => states.push(state));

    const record = createTestTelemetryRecord();
    manager.handleMessage({ type: 'telemetry', payload: record });

    expect(states).toHaveLength(1);
    expect(states[0].latestTelemetry).toEqual(record);
  });

  it('unsubscribe stops notifications', () => {
    const manager = new StateManager();
    const states: any[] = [];
    const unsubscribe = manager.subscribe((state) => states.push(state));

    manager.handleMessage({ type: 'telemetry', payload: createTestTelemetryRecord() });
    expect(states).toHaveLength(1);

    unsubscribe();
    manager.handleMessage({ type: 'telemetry', payload: createTestTelemetryRecord({ sequence_number: 2 }) });
    expect(states).toHaveLength(1);
  });

  it('reset restores initial state', () => {
    const manager = new StateManager();
    manager.handleMessage({ type: 'telemetry', payload: createTestTelemetryRecord() });
    expect(manager.getState().latestTelemetry).not.toBeNull();

    manager.reset();
    expect(manager.getState().latestTelemetry).toBeNull();
    expect(manager.getState().timeSeries.speedSuperHeavy).toHaveLength(0);
  });
});

describe('appendToTimeSeries', () => {
  it('appends speed data to correct store based on stage assignment', () => {
    const store = createInitialState().timeSeries;
    const record = createTestTelemetryRecord({
      stage_assignment_left: 'super_heavy',
      stage_assignment_right: 'starship',
    });

    const result = appendToTimeSeries(store, record);

    expect(result.speedSuperHeavy).toHaveLength(1);
    expect(result.speedSuperHeavy[0].value).toBe(1200.5);
    expect(result.speedStarship).toHaveLength(1);
    expect(result.speedStarship[0].value).toBe(1300.0);
  });

  it('appends altitude data to correct store based on stage assignment', () => {
    const store = createInitialState().timeSeries;
    const record = createTestTelemetryRecord({
      stage_assignment_left: 'super_heavy',
      stage_assignment_right: 'starship',
    });

    const result = appendToTimeSeries(store, record);

    expect(result.altitudeSuperHeavy).toHaveLength(1);
    expect(result.altitudeSuperHeavy[0].value).toBe(45.2);
    expect(result.altitudeStarship).toHaveLength(1);
    expect(result.altitudeStarship[0].value).toBe(50.1);
  });

  it('does not append if mission_elapsed_time is null', () => {
    const store = createInitialState().timeSeries;
    const record = createTestTelemetryRecord({ mission_elapsed_time: null });

    const result = appendToTimeSeries(store, record);

    expect(result.speedSuperHeavy).toHaveLength(0);
    expect(result.speedStarship).toHaveLength(0);
    expect(result.altitudeSuperHeavy).toHaveLength(0);
    expect(result.altitudeStarship).toHaveLength(0);
  });

  it('does not append if speed value is null', () => {
    const store = createInitialState().timeSeries;
    const record = createTestTelemetryRecord({
      speed_left: { value: null, unit: null, status: 'unavailable' },
      speed_right: { value: null, unit: null, status: 'unavailable' },
    });

    const result = appendToTimeSeries(store, record);

    expect(result.speedSuperHeavy).toHaveLength(0);
    expect(result.speedStarship).toHaveLength(0);
  });

  it('does not append if status is not available', () => {
    const store = createInitialState().timeSeries;
    const record = createTestTelemetryRecord({
      speed_left: { value: 100, unit: 'KM/H', status: 'occluded_by_engines' },
    });

    const result = appendToTimeSeries(store, record);

    expect(result.speedSuperHeavy).toHaveLength(0);
  });

  it('preserves existing data when appending', () => {
    const store = createInitialState().timeSeries;
    const record1 = createTestTelemetryRecord({ timestamp: 1700000000000 });
    const store1 = appendToTimeSeries(store, record1);

    const record2 = createTestTelemetryRecord({
      sequence_number: 2,
      timestamp: 1700000001000,
      speed_left: { value: 1400, unit: 'KM/H', status: 'available' },
    });
    const store2 = appendToTimeSeries(store1, record2);

    expect(store2.speedSuperHeavy).toHaveLength(2);
    expect(store2.speedSuperHeavy[0].value).toBe(1200.5);
    expect(store2.speedSuperHeavy[1].value).toBe(1400);
  });

  it('assigns both sides to both stores in pre-separation', () => {
    const store = createInitialState().timeSeries;
    const record = createTestTelemetryRecord({
      separation_state: 'pre_separation',
      stage_assignment_left: 'super_heavy',
      stage_assignment_right: 'super_heavy',
    });

    const result = appendToTimeSeries(store, record);

    // Pre-separation: data goes to both superheavy and starship
    expect(result.speedSuperHeavy).toHaveLength(2);
    expect(result.speedStarship).toHaveLength(2);
    expect(result.altitudeSuperHeavy).toHaveLength(2);
    expect(result.altitudeStarship).toHaveLength(2);
  });

  it('records timestamp and unit in time-series points', () => {
    const store = createInitialState().timeSeries;
    const record = createTestTelemetryRecord();

    const result = appendToTimeSeries(store, record);

    expect(result.speedSuperHeavy[0].timestamp).toBe(1700000000000);
    expect(result.speedSuperHeavy[0].unit).toBe('KM/H');
    expect(result.speedSuperHeavy[0].missionElapsedTime).toBe('T+00:01:30');
  });
});
