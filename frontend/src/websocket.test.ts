import { describe, it, expect } from 'vitest';
import { parseWebSocketMessage, serializeCommand } from './websocket';
import type { ControlCommand } from './types';

describe('parseWebSocketMessage', () => {
  it('parses a valid telemetry message', () => {
    const data = JSON.stringify({
      type: 'telemetry',
      payload: {
        sequence_number: 1,
        mission_elapsed_time: 'T+00:01:30',
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
        starship_engines: { e1: 'active', e2: 'active', e3: 'inactive', e4: 'active', e5: 'active', e6: 'undetected' },
        superheavy_engines: Object.fromEntries(
          Array.from({ length: 33 }, (_, i) => [`e${i + 1}`, 'inactive'])
        ),
        detection_accuracy: { starship: 0.83, superheavy: 0.97 },
        timestamp: 1700000000000,
      },
    });

    const result = parseWebSocketMessage(data);
    expect(result).not.toBeNull();
    expect(result!.type).toBe('telemetry');
    expect((result!.payload as any).sequence_number).toBe(1);
  });

  it('parses a valid status message', () => {
    const data = JSON.stringify({
      type: 'status',
      payload: {
        status: 'running',
        gpu: { available: true, device_name: 'NVIDIA RTX 4090' },
        frame_interval_ms: 1000,
        current_sequence: 42,
      },
    });

    const result = parseWebSocketMessage(data);
    expect(result).not.toBeNull();
    expect(result!.type).toBe('status');
    expect((result!.payload as any).status).toBe('running');
  });

  it('parses a valid error message', () => {
    const data = JSON.stringify({
      type: 'error',
      payload: { code: 'STREAM_DISCONNECTED', message: 'Video source lost' },
    });

    const result = parseWebSocketMessage(data);
    expect(result).not.toBeNull();
    expect(result!.type).toBe('error');
    expect((result!.payload as any).code).toBe('STREAM_DISCONNECTED');
  });

  it('returns null for invalid type', () => {
    const data = JSON.stringify({
      type: 'unknown',
      payload: {},
    });

    const result = parseWebSocketMessage(data);
    expect(result).toBeNull();
  });

  it('returns null for missing type field', () => {
    const data = JSON.stringify({ payload: {} });
    const result = parseWebSocketMessage(data);
    expect(result).toBeNull();
  });

  it('returns null for missing payload field', () => {
    const data = JSON.stringify({ type: 'telemetry' });
    const result = parseWebSocketMessage(data);
    expect(result).toBeNull();
  });

  it('returns null for non-object payload', () => {
    const data = JSON.stringify({ type: 'telemetry', payload: 'string' });
    const result = parseWebSocketMessage(data);
    expect(result).toBeNull();
  });

  it('throws for non-JSON input', () => {
    expect(() => parseWebSocketMessage('not json')).toThrow();
  });
});

describe('serializeCommand', () => {
  it('serializes start command', () => {
    const cmd: ControlCommand = { action: 'start', source_url: 'https://example.com/stream', interval_ms: 500 };
    const result = serializeCommand(cmd);
    const parsed = JSON.parse(result);
    expect(parsed.action).toBe('start');
    expect(parsed.source_url).toBe('https://example.com/stream');
    expect(parsed.interval_ms).toBe(500);
  });

  it('serializes stop command', () => {
    const cmd: ControlCommand = { action: 'stop' };
    const result = serializeCommand(cmd);
    const parsed = JSON.parse(result);
    expect(parsed.action).toBe('stop');
  });

  it('serializes validate_url command', () => {
    const cmd: ControlCommand = { action: 'validate_url', url: 'https://example.com/stream' };
    const result = serializeCommand(cmd);
    const parsed = JSON.parse(result);
    expect(parsed.action).toBe('validate_url');
    expect(parsed.url).toBe('https://example.com/stream');
  });

  it('start command without optional interval_ms', () => {
    const cmd: ControlCommand = { action: 'start', source_url: 'https://example.com/stream' };
    const result = serializeCommand(cmd);
    const parsed = JSON.parse(result);
    expect(parsed.action).toBe('start');
    expect(parsed.source_url).toBe('https://example.com/stream');
    expect(parsed.interval_ms).toBeUndefined();
  });
});
