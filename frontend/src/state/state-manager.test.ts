import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { StateManager } from './state-manager';
import type { TelemetryRecord, PipelineStatus, ErrorPayload, WebSocketMessage } from '../core/types';

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

  it('keeps every telemetry record for download', () => {
    const manager = new StateManager();
    const first = createTestTelemetryRecord({ sequence_number: 1 });
    // A record without MET adds no graph points but is still kept
    const second = createTestTelemetryRecord({ sequence_number: 2, mission_elapsed_time: null });

    manager.handleMessage({ type: 'telemetry', payload: first });
    manager.handleMessage({ type: 'telemetry', payload: second });

    expect(manager.getState().telemetryRecords).toEqual([first, second]);
    manager.reset();
    expect(manager.getState().telemetryRecords).toEqual([]);
  });

  it('detects engine events from live telemetry and clears them on reset', () => {
    const manager = new StateManager();
    const engines = (offAt: number, t: number) =>
      Object.fromEntries(
        Array.from({ length: 33 }, (_, i) => [`sh_e${i + 1}`, i === 19 && t >= offAt ? 'inactive' : 'active'] as const)
      );
    // 5 frames per second from MET 50 to 70; sh_e20 shuts down at 60
    for (let k = 0; k < 100; k++) {
      const t = 50 + k / 5;
      const s = Math.floor(t);
      manager.handleMessage({
        type: 'telemetry',
        payload: createTestTelemetryRecord({
          sequence_number: k,
          mission_elapsed_time: `T+00:${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`,
          timestamp: t * 1000,
          starship_engines: {},
          superheavy_engines: engines(60, t),
        }),
      });
    }

    const state = manager.getState();
    expect(state.engineEvents).toHaveLength(1);
    expect(state.engineEvents[0]).toMatchObject({ kind: 'engine_out', vehicle: 'superheavy', engineIds: ['sh_e20'] });
    expect(state.engineEvents[0].metSeconds).toBeCloseTo(60, 5);
    expect(state.engineEventsVersion).toBe(1);

    manager.reset();
    expect(manager.getState().engineEvents).toEqual([]);
    expect(manager.getState().engineEventsVersion).toBe(0);
  });

  it('records the MET of the first post-separation record and clears it on reset', () => {
    const manager = new StateManager();
    const send = (seq: number, met: string, separation: TelemetryRecord['separation_state']) =>
      manager.handleMessage({
        type: 'telemetry',
        payload: createTestTelemetryRecord({
          sequence_number: seq,
          mission_elapsed_time: met,
          separation_state: separation,
          timestamp: 1700000000000 + seq * 1000,
        }),
      });

    send(1, 'T+00:02:40', 'pre_separation');
    expect(manager.getState().separationMetSeconds).toBeNull();
    send(2, 'T+00:02:41', 'post_separation');
    expect(manager.getState().separationMetSeconds).toBeCloseTo(161, 0);
    send(3, 'T+00:02:42', 'post_separation');
    expect(manager.getState().separationMetSeconds).toBeCloseTo(161, 0);

    manager.reset();
    expect(manager.getState().separationMetSeconds).toBeNull();
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

describe('StateManager sub-second MET', () => {
  function telemetry(met: string, timestamp: number): WebSocketMessage {
    return { type: 'telemetry', payload: createTestTelemetryRecord({ mission_elapsed_time: met, timestamp }) };
  }

  it('spreads records within one MET second by frame timestamp', () => {
    const manager = new StateManager();
    manager.handleMessage(telemetry('T+00:01:30', 10_000));
    manager.handleMessage(telemetry('T+00:01:30', 10_333));
    manager.handleMessage(telemetry('T+00:01:30', 10_667));

    const xs = manager.getState().timeSeries.speedSuperHeavy.map((p) => p.metSeconds);
    expect(xs[0]).toBe(90);
    expect(xs[1]).toBeCloseTo(90.333, 10);
    expect(xs[2]).toBeCloseTo(90.667, 10);
  });

  it('forgets the anchor on reset', () => {
    const manager = new StateManager();
    manager.handleMessage(telemetry('T+00:01:30', 10_000));
    manager.reset();
    manager.handleMessage(telemetry('T+00:01:30', 10_500));

    expect(manager.getState().timeSeries.speedSuperHeavy[0].metSeconds).toBe(90);
  });
});


describe('StateManager timeSeriesVersion', () => {
  it('bumps only when telemetry adds points, keeping the series arrays in place', () => {
    const manager = new StateManager();
    const series = manager.getState().timeSeries.speedSuperHeavy;
    expect(manager.getState().timeSeriesVersion).toBe(0);

    manager.handleMessage({ type: 'telemetry', payload: createTestTelemetryRecord() });
    expect(manager.getState().timeSeriesVersion).toBe(1);
    expect(manager.getState().timeSeries.speedSuperHeavy).toBe(series);
    expect(series).toHaveLength(1);

    manager.handleMessage({ type: 'frame', payload: { image: '', sequence: 1, processing_fps: 0 } });
    expect(manager.getState().timeSeriesVersion).toBe(1);
  });
});

describe('StateManager replay', () => {
  function replay(chunk: number, records: TelemetryRecord[], runId = 'run-1'): WebSocketMessage {
    return {
      type: 'replay',
      payload: { run_id: runId, chunk, final: true, truncated: false, records },
    };
  }

  function rec(seq: number): TelemetryRecord {
    const s = 90 + seq;
    return createTestTelemetryRecord({
      sequence_number: seq,
      mission_elapsed_time: `T+00:${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`,
      timestamp: 1700000000000 + seq * 1000,
    });
  }

  it('chunk 0 clears earlier live data but keeps the pipeline status', () => {
    const manager = new StateManager();
    const status: PipelineStatus = {
      status: 'running',
      gpu: { available: false, device_name: null },
      skip_frames: 30,
      current_sequence: 1,
    };
    manager.handleMessage({ type: 'status', payload: status });
    manager.handleMessage({ type: 'telemetry', payload: rec(1) });
    const oldSeries = manager.getState().timeSeries;

    manager.handleMessage(replay(0, [rec(5), rec(6)]));

    const state = manager.getState();
    expect(state.pipelineStatus).toEqual(status);
    expect(state.telemetryRecords.map((r) => r.sequence_number)).toEqual([5, 6]);
    expect(state.timeSeries).not.toBe(oldSeries);
    expect(state.timeSeries.speedSuperHeavy).toHaveLength(2);
    expect(state.latestTelemetry?.sequence_number).toBe(6);
  });

  it('an empty chunk 0 (a new Run) leaves no live data', () => {
    const manager = new StateManager();
    manager.handleMessage({ type: 'telemetry', payload: rec(1) });
    manager.handleMessage(replay(0, []));

    const state = manager.getState();
    expect(state.telemetryRecords).toEqual([]);
    expect(state.latestTelemetry).toBeNull();
    expect(state.timeSeries.speedSuperHeavy).toHaveLength(0);
    expect(state.separationMetSeconds).toBeNull();
  });

  it('later chunks append, and live telemetry continues after them', () => {
    const manager = new StateManager();
    manager.handleMessage(replay(0, [rec(1), rec(2)]));
    manager.handleMessage(replay(1, [rec(3)]));
    manager.handleMessage({ type: 'telemetry', payload: rec(4) });

    const state = manager.getState();
    expect(state.telemetryRecords.map((r) => r.sequence_number)).toEqual([1, 2, 3, 4]);
    expect(state.timeSeries.speedStarship.map((p) => p.metSeconds)).toEqual([91, 92, 93, 94]);
  });

  it('notifies once per chunk and does not tick the FPS meter', () => {
    const manager = new StateManager();
    let calls = 0;
    manager.subscribe(() => calls++);

    manager.handleMessage(replay(0, [rec(1), rec(2), rec(3)]));
    expect(calls).toBe(1);
    manager.handleMessage(replay(1, [rec(4), rec(5)]));
    expect(calls).toBe(2);
    expect(manager.getState().processingFps).toBe(0);
  });

  it('gives the same data as live delivery', () => {
    const records = [1, 2, 3, 4, 5].map(rec);
    const live = new StateManager();
    for (const r of records) live.handleMessage({ type: 'telemetry', payload: r });
    const replayed = new StateManager();
    replayed.handleMessage(replay(0, records.slice(0, 2)));
    replayed.handleMessage(replay(1, records.slice(2)));

    const a = live.getState();
    const b = replayed.getState();
    expect(b.timeSeries).toEqual(a.timeSeries);
    expect(b.telemetryRecords).toEqual(a.telemetryRecords);
    expect(b.engineEvents).toEqual(a.engineEvents);
    expect(b.separationMetSeconds).toEqual(a.separationMetSeconds);
    expect(b.latestTelemetry).toEqual(a.latestTelemetry);
  });
});

describe('StateManager replay properties', () => {
  const statuses = ['active', 'inactive', 'undetected'] as const;

  /** Records with MET strings (or none), separation flips and engine statuses; timestamps rise. */
  const recordsArb = fc
    .array(
      fc.record({
        met: fc.option(fc.integer({ min: 0, max: 7200 }), { nil: null }),
        sep: fc.constantFrom('pre_separation', 'post_separation') as fc.Arbitrary<TelemetryRecord['separation_state']>,
        engines: fc.array(fc.constantFrom(...statuses), { minLength: 33, maxLength: 33 }),
        gap: fc.integer({ min: 1, max: 5000 }),
      }),
      { maxLength: 40 },
    )
    .map((items) => {
      let timestamp = 1700000000000;
      return items.map((it, i) => {
        timestamp += it.gap;
        const met = it.met;
        const text =
          met === null
            ? null
            : `T+${String(Math.floor(met / 3600)).padStart(2, '0')}:${String(Math.floor(met / 60) % 60).padStart(2, '0')}:${String(met % 60).padStart(2, '0')}`;
        return createTestTelemetryRecord({
          sequence_number: i + 1,
          mission_elapsed_time: text,
          separation_state: it.sep,
          superheavy_engines: Object.fromEntries(it.engines.map((st, k) => [`sh_e${k + 1}`, st])),
          timestamp,
        });
      });
    });

  /** Cut points into chunks: any split of the array, including empty chunks. */
  const splitArb = (n: number) => fc.array(fc.integer({ min: 0, max: n }), { maxLength: 6 });

  function chunked<T>(items: T[], cuts: number[]): T[][] {
    const points = [0, ...[...cuts].sort((a, b) => a - b), items.length];
    const chunks: T[][] = [];
    for (let i = 0; i + 1 < points.length; i++) chunks.push(items.slice(points[i], points[i + 1]));
    return chunks;
  }

  function replayMessage(chunk: number, records: TelemetryRecord[]): WebSocketMessage {
    return { type: 'replay', payload: { run_id: 'r', chunk, final: false, truncated: false, records } };
  }

  function feedReplay(manager: StateManager, records: TelemetryRecord[], cuts: number[]): void {
    chunked(records, cuts).forEach((c, i) => manager.handleMessage(replayMessage(i, c)));
  }

  function comparable(manager: StateManager) {
    const s = manager.getState();
    return {
      timeSeries: s.timeSeries,
      telemetryRecords: s.telemetryRecords,
      engineEvents: s.engineEvents,
      separationMetSeconds: s.separationMetSeconds,
      latestTelemetry: s.latestTelemetry,
    };
  }

  // Property 8
  it('replay in any chunking equals reset then live delivery', () => {
    fc.assert(
      fc.property(recordsArb.chain((r) => fc.tuple(fc.constant(r), splitArb(r.length))), ([records, cuts]) => {
        const live = new StateManager();
        live.reset();
        for (const r of records) live.handleMessage({ type: 'telemetry', payload: r });
        const replayed = new StateManager();
        feedReplay(replayed, records, cuts);
        // The empty case: replay with no records leaves a reset state, as live does
        if (records.length === 0) replayed.handleMessage(replayMessage(0, []));
        expect(comparable(replayed)).toEqual(comparable(live));
      }),
      { numRuns: 100 },
    );
  });

  // Property 9
  it('chunk 0 resets: live records followed by a replay equal that replay alone', () => {
    fc.assert(
      fc.property(
        recordsArb,
        recordsArb.chain((b) => fc.tuple(fc.constant(b), splitArb(b.length))),
        (a, [b, cuts]) => {
          const reconnected = new StateManager();
          for (const r of a) reconnected.handleMessage({ type: 'telemetry', payload: r });
          feedReplay(reconnected, b, cuts);
          if (b.length === 0) reconnected.handleMessage(replayMessage(0, []));
          const alone = new StateManager();
          feedReplay(alone, b, cuts);
          if (b.length === 0) alone.handleMessage(replayMessage(0, []));
          expect(comparable(reconnected)).toEqual(comparable(alone));
        },
      ),
      { numRuns: 100 },
    );
  });

  // Property 10
  it('notifies once per replay message and never changes processingFps', () => {
    fc.assert(
      fc.property(recordsArb.chain((r) => fc.tuple(fc.constant(r), splitArb(r.length))), ([records, cuts]) => {
        const manager = new StateManager();
        let calls = 0;
        manager.subscribe(() => calls++);
        const chunks = chunked(records, cuts);
        chunks.forEach((c, i) => {
          const before = calls;
          const fps = manager.getState().processingFps;
          manager.handleMessage(replayMessage(i, c));
          expect(calls - before).toBe(1);
          expect(manager.getState().processingFps).toBe(fps);
        });
      }),
      { numRuns: 100 },
    );
  });
});
