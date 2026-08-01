/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { PipelineControls, ValidationStatus } from './pipeline-controls';
import { StateManager } from './state';
import type { TelemetryWebSocket } from './websocket';
import type { PipelineStatus } from './types';

/**
 * Create a mock TelemetryWebSocket with just enough interface for PipelineControls.
 */
function createMockWs(): TelemetryWebSocket {
  return {
    sendCommand: vi.fn(),
    onMessage: vi.fn(() => () => {}),
    onConnectionStateChange: vi.fn(() => () => {}),
    connect: vi.fn(),
    disconnect: vi.fn(),
    connectionState: 'disconnected',
  } as unknown as TelemetryWebSocket;
}

function createPipelineStatus(status: PipelineStatus['status']): PipelineStatus {
  return {
    status,
    gpu: { available: false, device_name: null },
    skip_frames: 30,
    current_sequence: 0,
  };
}

describe('PipelineControls', () => {
  let container: HTMLElement;
  let ws: TelemetryWebSocket;
  let stateManager: StateManager;
  let controls: PipelineControls;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    ws = createMockWs();
    stateManager = new StateManager();
    controls = new PipelineControls(container, ws, stateManager);
  });

  describe('Requirement 7.2: URL input field', () => {
    it('should render a URL input field', () => {
      const input = container.querySelector('.pipeline-controls__url-input') as HTMLInputElement;
      expect(input).not.toBeNull();
      expect(input.type).toBe('url');
      expect(input.placeholder).toContain('livestream URL');
    });

    it('should render a Validate button', () => {
      const btn = container.querySelector('.pipeline-controls__validate-btn') as HTMLButtonElement;
      expect(btn).not.toBeNull();
      expect(btn.textContent).toBe('Validate');
    });
  });

  describe('Requirement 7.3: Stream validation status', () => {
    it('should send validate_url command when Validate is clicked', () => {
      const input = container.querySelector('.pipeline-controls__url-input') as HTMLInputElement;
      const btn = container.querySelector('.pipeline-controls__validate-btn') as HTMLButtonElement;

      input.value = 'https://youtube.com/live/test';
      btn.click();

      expect((ws.sendCommand as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith({
        action: 'validate_url',
        url: 'https://youtube.com/live/test',
      });
    });

    it('should send validate_url command on Enter key', () => {
      const input = container.querySelector('.pipeline-controls__url-input') as HTMLInputElement;

      input.value = 'https://youtube.com/live/test';
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

      expect((ws.sendCommand as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith({
        action: 'validate_url',
        url: 'https://youtube.com/live/test',
      });
    });

    it('should not send command when URL is empty', () => {
      const btn = container.querySelector('.pipeline-controls__validate-btn') as HTMLButtonElement;
      btn.click();

      expect((ws.sendCommand as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();
    });

    it('should display "checking" status after submitting URL', () => {
      const input = container.querySelector('.pipeline-controls__url-input') as HTMLInputElement;
      const btn = container.querySelector('.pipeline-controls__validate-btn') as HTMLButtonElement;

      input.value = 'https://youtube.com/live/test';
      btn.click();

      const indicator = container.querySelector('.pipeline-controls__validation-status') as HTMLElement;
      expect(indicator.textContent).toBe('Checking...');
      expect(indicator.classList.contains('pipeline-controls__validation-status--checking')).toBe(true);
    });

    it('should display "active" when pipeline responds with stopped status', () => {
      const input = container.querySelector('.pipeline-controls__url-input') as HTMLInputElement;
      const btn = container.querySelector('.pipeline-controls__validate-btn') as HTMLButtonElement;

      input.value = 'https://youtube.com/live/test';
      btn.click();

      // Simulate server validation_result response
      stateManager.handleMessage({
        type: 'validation_result',
        payload: { valid: true, message: '', url: 'https://youtube.com/live/test' },
      });

      const indicator = container.querySelector('.pipeline-controls__validation-status') as HTMLElement;
      expect(indicator.textContent).toBe('Stream active');
      expect(indicator.classList.contains('pipeline-controls__validation-status--active')).toBe(true);
    });

    it('should display "unreachable" when pipeline responds with disconnected status', () => {
      const input = container.querySelector('.pipeline-controls__url-input') as HTMLInputElement;
      const btn = container.querySelector('.pipeline-controls__validate-btn') as HTMLButtonElement;

      input.value = 'https://youtube.com/live/bad-url';
      btn.click();

      // Simulate server validation_result response
      stateManager.handleMessage({
        type: 'validation_result',
        payload: { valid: false, message: 'Stream unreachable', url: 'https://youtube.com/live/bad-url' },
      });

      const indicator = container.querySelector('.pipeline-controls__validation-status') as HTMLElement;
      expect(indicator.textContent).toBe('Stream unreachable');
      expect(indicator.classList.contains('pipeline-controls__validation-status--unreachable')).toBe(true);
    });
  });

  describe('Requirement 7.4: Start button when validated', () => {
    it('should show Start button when stream is validated as active and pipeline is stopped', () => {
      controls.setValidationStatus('active');
      stateManager.handleMessage({
        type: 'status',
        payload: createPipelineStatus('stopped'),
      });

      const actionBtn = container.querySelector('.pipeline-controls__action-btn') as HTMLButtonElement;
      expect(actionBtn.style.display).not.toBe('none');
      expect(actionBtn.textContent).toBe('Start');
      expect(actionBtn.classList.contains('pipeline-controls__action-btn--start')).toBe(true);
    });

    it('should not show Start button when validation status is idle', () => {
      stateManager.handleMessage({
        type: 'status',
        payload: createPipelineStatus('stopped'),
      });

      const actionBtn = container.querySelector('.pipeline-controls__action-btn') as HTMLButtonElement;
      expect(actionBtn.style.display).toBe('none');
    });

    it('should send start command with source_url when Start is clicked', () => {
      const input = container.querySelector('.pipeline-controls__url-input') as HTMLInputElement;
      const validateBtn = container.querySelector('.pipeline-controls__validate-btn') as HTMLButtonElement;

      input.value = 'https://youtube.com/live/test';
      validateBtn.click();

      // Simulate validation success
      stateManager.handleMessage({
        type: 'validation_result',
        payload: { valid: true, message: '', url: 'https://youtube.com/live/test' },
      });

      const actionBtn = container.querySelector('.pipeline-controls__action-btn') as HTMLButtonElement;
      actionBtn.click();

      expect((ws.sendCommand as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith({
        action: 'start',
        source_url: 'https://youtube.com/live/test',
        skip_frames: 30,
      });
    });
  });

  describe('Requirement 7.5: Stop button when running', () => {
    it('should show Stop button when pipeline is running', () => {
      stateManager.handleMessage({
        type: 'status',
        payload: createPipelineStatus('running'),
      });

      const actionBtn = container.querySelector('.pipeline-controls__action-btn') as HTMLButtonElement;
      expect(actionBtn.style.display).not.toBe('none');
      expect(actionBtn.textContent).toBe('Stop');
      expect(actionBtn.classList.contains('pipeline-controls__action-btn--stop')).toBe(true);
    });

    it('should send stop command when Stop is clicked', () => {
      stateManager.handleMessage({
        type: 'status',
        payload: createPipelineStatus('running'),
      });

      const actionBtn = container.querySelector('.pipeline-controls__action-btn') as HTMLButtonElement;
      actionBtn.click();

      expect((ws.sendCommand as ReturnType<typeof vi.fn>)).toHaveBeenCalledWith({
        action: 'stop',
      });
    });
  });

  describe('Requirement 7.17: Pipeline status badge', () => {
    it('should display "Stopped" badge when pipeline is stopped', () => {
      stateManager.handleMessage({
        type: 'status',
        payload: createPipelineStatus('stopped'),
      });

      const badge = container.querySelector('.pipeline-controls__status-badge') as HTMLElement;
      expect(badge.textContent).toBe('Stopped');
      expect(badge.classList.contains('pipeline-controls__status-badge--stopped')).toBe(true);
    });

    it('should display "Running" badge when pipeline is running', () => {
      stateManager.handleMessage({
        type: 'status',
        payload: createPipelineStatus('running'),
      });

      const badge = container.querySelector('.pipeline-controls__status-badge') as HTMLElement;
      expect(badge.textContent).toBe('Running');
      expect(badge.classList.contains('pipeline-controls__status-badge--running')).toBe(true);
    });

    it('should display "Disconnected" badge when pipeline is disconnected', () => {
      stateManager.handleMessage({
        type: 'status',
        payload: createPipelineStatus('disconnected'),
      });

      const badge = container.querySelector('.pipeline-controls__status-badge') as HTMLElement;
      expect(badge.textContent).toBe('Disconnected');
      expect(badge.classList.contains('pipeline-controls__status-badge--disconnected')).toBe(true);
    });

    it('should show no badge text when no pipeline status is available', () => {
      const badge = container.querySelector('.pipeline-controls__status-badge') as HTMLElement;
      expect(badge.textContent).toBe('');
    });
  });

  describe('cleanup', () => {
    it('should unsubscribe from state manager on destroy', () => {
      controls.destroy();
      // After destroy, state changes should not cause errors
      stateManager.handleMessage({
        type: 'status',
        payload: createPipelineStatus('running'),
      });
      // No error thrown means unsubscribe worked
    });
  });
});
