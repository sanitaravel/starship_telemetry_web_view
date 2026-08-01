import type { TelemetryWebSocket } from './websocket';
import type { StateManager, AppState } from './state';
import type { PipelineStatus } from './types';

/**
 * Validation status for a submitted livestream URL.
 */
export type ValidationStatus = 'idle' | 'checking' | 'active' | 'unreachable';

/**
 * Pipeline Controls UI component.
 * Renders URL input, validation status, Start/Stop buttons, and pipeline status badge.
 * Integrates with TelemetryWebSocket for commands and StateManager for state updates.
 */
export class PipelineControls {
  private container: HTMLElement;
  private ws: TelemetryWebSocket;
  private stateManager: StateManager;

  private urlInput!: HTMLInputElement;
  private validateButton!: HTMLButtonElement;
  private validationIndicator!: HTMLElement;
  private actionButton!: HTMLButtonElement;
  private statusBadge!: HTMLElement;
  private intervalInput!: HTMLInputElement;
  private fpsIndicator!: HTMLElement;

  private validationStatus: ValidationStatus = 'idle';
  private currentUrl: string = '';
  private unsubscribe: (() => void) | null = null;

  constructor(container: HTMLElement, ws: TelemetryWebSocket, stateManager: StateManager) {
    this.container = container;
    this.ws = ws;
    this.stateManager = stateManager;

    this.render();
    this.bindEvents();
    this.subscribeToState();
    this.update(this.stateManager.getState());
  }

  /**
   * Build the DOM structure for pipeline controls.
   */
  private render(): void {
    this.container.innerHTML = '';
    this.container.classList.add('pipeline-controls');

    // URL input row
    const urlRow = document.createElement('div');
    urlRow.className = 'pipeline-controls__url-row';

    this.urlInput = document.createElement('input');
    this.urlInput.type = 'url';
    this.urlInput.placeholder = 'Paste livestream URL...';
    this.urlInput.className = 'pipeline-controls__url-input';
    this.urlInput.setAttribute('aria-label', 'Livestream URL');

    this.validateButton = document.createElement('button');
    this.validateButton.textContent = 'Validate';
    this.validateButton.className = 'pipeline-controls__validate-btn';
    this.validateButton.type = 'button';

    urlRow.appendChild(this.urlInput);
    urlRow.appendChild(this.validateButton);

    // Interval control row
    const intervalRow = document.createElement('div');
    intervalRow.className = 'pipeline-controls__interval-row';

    const intervalLabel = document.createElement('label');
    intervalLabel.className = 'pipeline-controls__interval-label';
    intervalLabel.textContent = 'Process every:';
    intervalLabel.setAttribute('for', 'interval-input');

    this.intervalInput = document.createElement('input');
    this.intervalInput.type = 'number';
    this.intervalInput.id = 'interval-input';
    this.intervalInput.min = '1';
    this.intervalInput.max = '300';
    this.intervalInput.step = '1';
    this.intervalInput.value = '30';
    this.intervalInput.className = 'pipeline-controls__interval-input';
    this.intervalInput.setAttribute('aria-label', 'Process every Nth frame');

    const intervalUnit = document.createElement('span');
    intervalUnit.className = 'pipeline-controls__interval-unit';
    intervalUnit.textContent = 'th frame';

    intervalRow.appendChild(intervalLabel);
    intervalRow.appendChild(this.intervalInput);
    intervalRow.appendChild(intervalUnit);

    // Status row
    const statusRow = document.createElement('div');
    statusRow.className = 'pipeline-controls__status-row';

    this.validationIndicator = document.createElement('span');
    this.validationIndicator.className = 'pipeline-controls__validation-status';
    this.validationIndicator.setAttribute('aria-live', 'polite');

    this.actionButton = document.createElement('button');
    this.actionButton.className = 'pipeline-controls__action-btn';
    this.actionButton.type = 'button';
    this.actionButton.style.display = 'none';

    this.statusBadge = document.createElement('span');
    this.statusBadge.className = 'pipeline-controls__status-badge';
    this.statusBadge.setAttribute('aria-live', 'polite');

    this.fpsIndicator = document.createElement('span');
    this.fpsIndicator.className = 'pipeline-controls__fps';
    this.fpsIndicator.setAttribute('aria-live', 'polite');

    statusRow.appendChild(this.validationIndicator);
    statusRow.appendChild(this.actionButton);
    statusRow.appendChild(this.fpsIndicator);
    statusRow.appendChild(this.statusBadge);

    this.container.appendChild(urlRow);
    this.container.appendChild(intervalRow);
    this.container.appendChild(statusRow);
  }

  /**
   * Bind DOM event listeners.
   */
  private bindEvents(): void {
    this.validateButton.addEventListener('click', () => this.handleValidate());
    this.urlInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        this.handleValidate();
      }
    });
    this.actionButton.addEventListener('click', () => this.handleAction());
  }

  /**
   * Subscribe to StateManager for pipeline status updates.
   */
  private subscribeToState(): void {
    this.unsubscribe = this.stateManager.subscribe((state) => this.update(state));
  }

  /**
   * Handle URL validation submission.
   */
  private handleValidate(): void {
    const url = this.urlInput.value.trim();
    if (!url) return;

    this.currentUrl = url;
    this.setValidationStatus('checking');
    this.ws.sendCommand({ action: 'validate_url', url });
  }

  /**
   * Handle Start/Stop button clicks.
   */
  private handleAction(): void {
    const state = this.stateManager.getState();
    const pipelineStatus = state.pipelineStatus?.status;

    if (pipelineStatus === 'running') {
      this.ws.sendCommand({ action: 'stop' });
    } else {
      const skipFrames = parseInt(this.intervalInput.value, 10) || 30;
      this.ws.sendCommand({ action: 'start', source_url: this.currentUrl, skip_frames: skipFrames });
    }
  }

  /**
   * Update UI based on new application state.
   */
  private update(state: AppState): void {
    const pipelineStatus = state.pipelineStatus;

    // Handle validation result from server
    if (state.latestValidationResult && this.validationStatus === 'checking') {
      if (state.latestValidationResult.valid) {
        this.setValidationStatus('active');
      } else {
        this.setValidationStatus('unreachable');
      }
    }

    this.renderValidationStatus();
    this.renderActionButton(pipelineStatus);
    this.renderStatusBadge(pipelineStatus);
    this.renderFps(pipelineStatus);
  }

  /**
   * When server sends a status message while we're checking,
   * infer validation result from pipeline status.
   */
  private updateValidationFromPipelineStatus(pipelineStatus: PipelineStatus): void {
    if (this.validationStatus === 'checking') {
      // If pipeline transitions to running or stays stopped (meaning URL was validated),
      // we consider the URL active. If disconnected, it's unreachable.
      if (pipelineStatus.status === 'stopped' || pipelineStatus.status === 'running') {
        this.setValidationStatus('active');
      } else if (pipelineStatus.status === 'disconnected') {
        this.setValidationStatus('unreachable');
      }
    }
  }

  /**
   * Set the validation status and update the indicator.
   */
  setValidationStatus(status: ValidationStatus): void {
    this.validationStatus = status;
    this.renderValidationStatus();
    this.renderActionButton(this.stateManager.getState().pipelineStatus);
  }

  /**
   * Render the validation status indicator.
   */
  private renderValidationStatus(): void {
    const indicator = this.validationIndicator;

    // Remove all status classes
    indicator.classList.remove(
      'pipeline-controls__validation-status--checking',
      'pipeline-controls__validation-status--active',
      'pipeline-controls__validation-status--unreachable'
    );

    switch (this.validationStatus) {
      case 'checking':
        indicator.textContent = 'Checking...';
        indicator.classList.add('pipeline-controls__validation-status--checking');
        break;
      case 'active':
        indicator.textContent = 'Stream active';
        indicator.classList.add('pipeline-controls__validation-status--active');
        break;
      case 'unreachable':
        indicator.textContent = 'Stream unreachable';
        indicator.classList.add('pipeline-controls__validation-status--unreachable');
        break;
      default:
        indicator.textContent = '';
        break;
    }
  }

  /**
   * Render Start/Stop action button based on pipeline state and validation status.
   */
  private renderActionButton(pipelineStatus: PipelineStatus | null): void {
    const btn = this.actionButton;
    const status = pipelineStatus?.status;

    if (status === 'running') {
      // Show Stop button when running
      btn.textContent = 'Stop';
      btn.style.display = '';
      btn.classList.remove('pipeline-controls__action-btn--start');
      btn.classList.add('pipeline-controls__action-btn--stop');
    } else if (this.validationStatus === 'active') {
      // Show Start button when validated and pipeline is not running
      btn.textContent = 'Start';
      btn.style.display = '';
      btn.classList.remove('pipeline-controls__action-btn--stop');
      btn.classList.add('pipeline-controls__action-btn--start');
    } else {
      // Hide button otherwise
      btn.style.display = 'none';
    }
  }

  /**
   * Render the pipeline status badge.
   */
  private renderStatusBadge(pipelineStatus: PipelineStatus | null): void {
    const badge = this.statusBadge;

    badge.classList.remove(
      'pipeline-controls__status-badge--stopped',
      'pipeline-controls__status-badge--running',
      'pipeline-controls__status-badge--disconnected',
      'pipeline-controls__status-badge--reconnecting'
    );

    if (!pipelineStatus) {
      badge.textContent = '';
      return;
    }

    const status = pipelineStatus.status;
    badge.textContent = status.charAt(0).toUpperCase() + status.slice(1);
    badge.classList.add(`pipeline-controls__status-badge--${status}`);
  }

  /**
   * Render the processing FPS indicator.
   */
  private renderFps(pipelineStatus: PipelineStatus | null): void {
    if (!pipelineStatus || pipelineStatus.status !== 'running' || !pipelineStatus.processing_fps) {
      this.fpsIndicator.textContent = '';
      return;
    }
    this.fpsIndicator.textContent = `${pipelineStatus.processing_fps.toFixed(1)} FPS`;
  }

  /**
   * Cleanup subscriptions.
   */
  destroy(): void {
    if (this.unsubscribe) {
      this.unsubscribe();
      this.unsubscribe = null;
    }
  }
}
