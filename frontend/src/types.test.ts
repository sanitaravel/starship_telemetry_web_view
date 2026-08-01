import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { TelemetryRecord, PipelineStatus, ControlCommand, WebSocketMessage } from './types';

describe('TypeScript interfaces', () => {
  it('TelemetryRecord can be constructed with valid data', () => {
    const record: TelemetryRecord = {
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
      starship_engines: { ss_e1: 'active', ss_e2: 'active', ss_e3: 'inactive', ss_e4: 'active', ss_e5: 'active', ss_e6: 'undetected' },
      superheavy_engines: Object.fromEntries(
        Array.from({ length: 33 }, (_, i) => [`sh_e${i + 1}`, 'inactive' as const])
      ),
      detection_accuracy: { starship: 0.83, superheavy: 0.97 },
      timestamp: Date.now(),
    };

    expect(record.sequence_number).toBe(1);
    expect(record.separation_state).toBe('post_separation');
    expect(Object.keys(record.starship_engines)).toHaveLength(6);
    expect(Object.keys(record.superheavy_engines)).toHaveLength(33);
  });

  it('PipelineStatus can be constructed with valid data', () => {
    const status: PipelineStatus = {
      status: 'running',
      gpu: { available: true, device_name: 'NVIDIA RTX 4090' },
      skip_frames: 30,
      current_sequence: 42,
    };

    expect(status.status).toBe('running');
    expect(status.gpu.available).toBe(true);
  });

  it('ControlCommand supports all action types', () => {
    const start: ControlCommand = { action: 'start', source_url: 'https://example.com/stream', skip_frames: 15 };
    const stop: ControlCommand = { action: 'stop' };
    const validate: ControlCommand = { action: 'validate_url', url: 'https://example.com/stream' };

    expect(start.action).toBe('start');
    expect(stop.action).toBe('stop');
    expect(validate.action).toBe('validate_url');
  });

  it('fast-check integration works with TelemetryRecord fields', () => {
    fc.assert(
      fc.property(
        fc.nat(),
        fc.double({ min: 0, max: 10000, noNaN: true }),
        (seq, speed) => {
          const record: TelemetryRecord = {
            sequence_number: seq,
            mission_elapsed_time: null,
            speed_left: { value: speed, unit: 'KM/H', status: 'available' },
            speed_right: { value: null, unit: null, status: 'unavailable' },
            altitude_left: { value: null, unit: null, status: 'unavailable' },
            altitude_right: { value: null, unit: null, status: 'unavailable' },
            stage_left_label: null,
            stage_right_label: null,
            stage_separation_text: null,
            stage_assignment_left: 'super_heavy',
            stage_assignment_right: 'super_heavy',
            separation_state: 'pre_separation',
            starship_engines: { ss_e1: 'undetected', ss_e2: 'undetected', ss_e3: 'undetected', ss_e4: 'undetected', ss_e5: 'undetected', ss_e6: 'undetected' },
            superheavy_engines: Object.fromEntries(
              Array.from({ length: 33 }, (_, i) => [`sh_e${i + 1}`, 'undetected' as const])
            ),
            detection_accuracy: { starship: 0, superheavy: 0 },
            timestamp: Date.now(),
          };

          return record.sequence_number >= 0 && record.speed_left.value === speed;
        }
      )
    );
  });
});
