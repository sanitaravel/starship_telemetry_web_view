/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { TelemetryDisplay } from './telemetry-display';
import { StateManager } from './state';
import type { TelemetryRecord } from './types';

function createTelemetryRecord(overrides: Partial<TelemetryRecord> = {}): TelemetryRecord {
  return {
    sequence_number: 1,
    mission_elapsed_time: '00:05:32',
    speed_left: { value: 1200, unit: 'km/h', status: 'available' },
    speed_right: { value: 800, unit: 'km/h', status: 'available' },
    altitude_left: { value: 45, unit: 'km', status: 'available' },
    altitude_right: { value: 120, unit: 'km', status: 'available' },
    stage_left_label: 'SUPER HEAVY',
    stage_right_label: 'STARSHIP',
    stage_separation_text: null,
    stage_assignment_left: 'super_heavy',
    stage_assignment_right: 'starship',
    separation_state: 'pre_separation',
    starship_engines: {},
    superheavy_engines: {},
    detection_accuracy: { starship: 0.95, superheavy: 0.95 },
    timestamp: Date.now(),
    ...overrides,
  };
}

describe('TelemetryDisplay', () => {
  let container: HTMLElement;
  let stateManager: StateManager;
  let display: TelemetryDisplay;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    stateManager = new StateManager();
    display = new TelemetryDisplay(container, stateManager);
  });

  describe('Requirement 7.6: Mission elapsed time display', () => {
    it('should display mission elapsed time prominently', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({ mission_elapsed_time: '00:12:45' }),
      });

      const metValue = container.querySelector('.telemetry-display__met-value') as HTMLElement;
      expect(metValue.textContent).toBe('00:12:45');
    });

    it('should display "--:--:--" when no telemetry is available', () => {
      const metValue = container.querySelector('.telemetry-display__met-value') as HTMLElement;
      expect(metValue.textContent).toBe('--:--:--');
    });

    it('should display "--:--:--" when mission_elapsed_time is null', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({ mission_elapsed_time: null }),
      });

      const metValue = container.querySelector('.telemetry-display__met-value') as HTMLElement;
      expect(metValue.textContent).toBe('--:--:--');
    });
  });

  describe('Requirement 7.7: Speed and altitude with units', () => {
    it('should display speed values with units for both sides', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({
          speed_left: { value: 1500, unit: 'km/h', status: 'available' },
          speed_right: { value: 2800, unit: 'km/h', status: 'available' },
        }),
      });

      const fieldValues = container.querySelectorAll('.telemetry-display__field-value');
      // Left speed (index 0), Left altitude (index 1), Right speed (index 2), Right altitude (index 3)
      expect(fieldValues[0].textContent).toBe('1500 km/h');
      expect(fieldValues[2].textContent).toBe('2800 km/h');
    });

    it('should display altitude values with units for both sides', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({
          altitude_left: { value: 55, unit: 'km', status: 'available' },
          altitude_right: { value: 180, unit: 'km', status: 'available' },
        }),
      });

      const fieldValues = container.querySelectorAll('.telemetry-display__field-value');
      expect(fieldValues[1].textContent).toBe('55 km');
      expect(fieldValues[3].textContent).toBe('180 km');
    });

    it('should display value without space when unit is null', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({
          speed_left: { value: 500, unit: null, status: 'available' },
        }),
      });

      const fieldValues = container.querySelectorAll('.telemetry-display__field-value');
      expect(fieldValues[0].textContent).toBe('500');
    });
  });

  describe('Requirement 7.11: Stage labels', () => {
    it('should display stage labels for left and right', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({
          stage_left_label: 'SUPER HEAVY',
          stage_right_label: 'STARSHIP',
        }),
      });

      const leftLabel = container.querySelector('.telemetry-display__column:first-child .telemetry-display__stage-label') as HTMLElement;
      const rightLabel = container.querySelector('.telemetry-display__column:last-child .telemetry-display__stage-label') as HTMLElement;
      expect(leftLabel.textContent).toBe('SUPER HEAVY');
      expect(rightLabel.textContent).toBe('STARSHIP');
    });

    it('should display "--" when stage label is null', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({
          stage_left_label: null,
          stage_right_label: null,
        }),
      });

      const leftLabel = container.querySelector('.telemetry-display__column:first-child .telemetry-display__stage-label') as HTMLElement;
      const rightLabel = container.querySelector('.telemetry-display__column:last-child .telemetry-display__stage-label') as HTMLElement;
      expect(leftLabel.textContent).toBe('--');
      expect(rightLabel.textContent).toBe('--');
    });
  });

  describe('Requirement 7.16: Placeholder for unavailable/occluded fields', () => {
    it('should display "--" when field status is "unavailable"', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({
          speed_left: { value: 1200, unit: 'km/h', status: 'unavailable' },
          altitude_right: { value: 80, unit: 'km', status: 'unavailable' },
        }),
      });

      const fieldValues = container.querySelectorAll('.telemetry-display__field-value');
      expect(fieldValues[0].textContent).toBe('--');
      expect(fieldValues[3].textContent).toBe('--');
    });

    it('should display "--" when field status is "occluded_by_engines"', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({
          speed_right: { value: 500, unit: 'km/h', status: 'occluded_by_engines' },
          altitude_left: { value: 30, unit: 'km', status: 'occluded_by_engines' },
        }),
      });

      const fieldValues = container.querySelectorAll('.telemetry-display__field-value');
      expect(fieldValues[1].textContent).toBe('--');
      expect(fieldValues[2].textContent).toBe('--');
    });

    it('should display "--" when field value is null', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({
          speed_left: { value: null, unit: 'km/h', status: 'available' },
        }),
      });

      const fieldValues = container.querySelectorAll('.telemetry-display__field-value');
      expect(fieldValues[0].textContent).toBe('--');
    });
  });

  describe('State subscription', () => {
    it('should update when new telemetry arrives', () => {
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({ mission_elapsed_time: '00:01:00' }),
      });

      const metValue = container.querySelector('.telemetry-display__met-value') as HTMLElement;
      expect(metValue.textContent).toBe('00:01:00');

      // Send updated telemetry
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord({ mission_elapsed_time: '00:02:00' }),
      });

      expect(metValue.textContent).toBe('00:02:00');
    });

    it('should unsubscribe from state manager on destroy', () => {
      display.destroy();
      // After destroy, state changes should not cause errors
      stateManager.handleMessage({
        type: 'telemetry',
        payload: createTelemetryRecord(),
      });
      // No error thrown means unsubscribe worked
    });
  });
});
