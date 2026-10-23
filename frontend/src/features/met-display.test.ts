/**
 * @vitest-environment jsdom
 */
import { describe, it, expect } from 'vitest';
import { MetDisplay } from './met-display';
import { StateManager } from '../state/state-manager';
import type { TelemetryRecord } from '../core/types';

function record(met: string | null): TelemetryRecord {
  return {
    sequence_number: 1,
    mission_elapsed_time: met,
    mission_elapsed_time_raw: met,
    speed_left: { value: null, unit: null, status: 'unavailable' },
    speed_right: { value: null, unit: null, status: 'unavailable' },
    altitude_left: { value: null, unit: null, status: 'unavailable' },
    altitude_right: { value: null, unit: null, status: 'unavailable' },
    stage_left_label: null,
    stage_right_label: null,
    stage_separation_text: null,
    stage_assignment_left: 'super_heavy',
    stage_assignment_right: 'super_heavy',
    separation_state: 'pre_separation',
    t_zero_found: true,
    stage_sep_found: false,
    starship_engines: {},
    superheavy_engines: {},
    detection_accuracy: { starship: 0, superheavy: 0 },
    timestamp: 1700000000000,
  };
}

describe('MetDisplay', () => {
  it('shows the placeholder, then the latest MET, and nothing else', () => {
    const container = document.createElement('div');
    const stateManager = new StateManager();
    new MetDisplay(container, stateManager);
    const value = container.querySelector('.met-display__value')!;
    expect(value.textContent).toBe('--:--:--');

    stateManager.handleMessage({ type: 'telemetry', payload: record('T+00:02:41') });
    expect(value.textContent).toBe('T+00:02:41');

    stateManager.handleMessage({ type: 'telemetry', payload: record(null) });
    expect(value.textContent).toBe('--:--:--');

    expect(container.querySelectorAll('button, select, input')).toHaveLength(0);
  });
});
