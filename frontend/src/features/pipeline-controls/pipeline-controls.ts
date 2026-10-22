import type { TelemetryWebSocket } from '../../api/websocket';
import type { StateManager, AppState } from '../../state/state-manager';
import { generateCorrelationId } from '../../core/correlation';
import { createLogger, setCorrelationId } from '../../core/logger';
import { renderPipelineControls } from './template';
import {
  renderValidationStatus,
  renderActionButton,
  renderStatusBadge,
  renderFps,
  type ValidationStatus,
} from './status';

const logger = createLogger('pipeline-controls');

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
    const elements = renderPipelineControls(this.container);
    this.urlInput = elements.urlInput;
    this.validateButton = elements.validateButton;
    this.validationIndicator = elements.validationIndicator;
    this.actionButton = elements.actionButton;
    this.statusBadge = elements.statusBadge;
    this.intervalInput = elements.intervalInput;
    this.fpsIndicator = elements.fpsIndicator;
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

    const correlationId = generateCorrelationId();
    const command = { action: 'validate_url' as const, url, correlation_id: correlationId };

    logger.debug(`Sending command: ${command.action}`, { correlation_id: correlationId });
    setCorrelationId(correlationId);
    this.ws.sendCommand(command);
    setCorrelationId(null);
  }

  /**
   * Handle Start/Stop button clicks.
   */
  private handleAction(): void {
    const state = this.stateManager.getState();
    const pipelineStatus = state.pipelineStatus?.status;

    const correlationId = generateCorrelationId();

    if (pipelineStatus === 'running') {
      const command = { action: 'stop' as const, correlation_id: correlationId };
      logger.debug(`Sending command: ${command.action}`, { correlation_id: correlationId });
      setCorrelationId(correlationId);
      this.ws.sendCommand(command);
      setCorrelationId(null);
    } else {
      const skipFrames = parseInt(this.intervalInput.value, 10) || 30;
      const command = { action: 'start' as const, source_url: this.currentUrl, skip_frames: skipFrames, correlation_id: correlationId };
      logger.debug(`Sending command: ${command.action}`, { correlation_id: correlationId });
      setCorrelationId(correlationId);
      this.ws.sendCommand(command);
      setCorrelationId(null);
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

    renderValidationStatus(this.validationIndicator, this.validationStatus);
    renderActionButton(this.actionButton, pipelineStatus, this.validationStatus);
    renderStatusBadge(this.statusBadge, pipelineStatus);
    renderFps(this.fpsIndicator, pipelineStatus, this.stateManager.getState().processingFps);
  }

  /**
   * Set the validation status and update the indicator.
   */
  setValidationStatus(status: ValidationStatus): void {
    this.validationStatus = status;
    renderValidationStatus(this.validationIndicator, this.validationStatus);
    renderActionButton(this.actionButton, this.stateManager.getState().pipelineStatus, this.validationStatus);
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
