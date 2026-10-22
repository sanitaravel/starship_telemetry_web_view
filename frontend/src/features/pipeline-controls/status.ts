import type { PipelineStatus } from '../../core/types';

/**
 * Validation status for a submitted livestream URL.
 */
export type ValidationStatus = 'idle' | 'checking' | 'active' | 'unreachable';

/**
 * Render the validation status indicator.
 */
export function renderValidationStatus(indicator: HTMLElement, validationStatus: ValidationStatus): void {
  // Remove all status classes
  indicator.classList.remove(
    'pipeline-controls__validation-status--checking',
    'pipeline-controls__validation-status--active',
    'pipeline-controls__validation-status--unreachable'
  );

  switch (validationStatus) {
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
export function renderActionButton(
  btn: HTMLButtonElement,
  pipelineStatus: PipelineStatus | null,
  validationStatus: ValidationStatus
): void {
  const status = pipelineStatus?.status;

  if (status === 'running') {
    // Show Stop button when running
    btn.textContent = 'Stop';
    btn.style.display = '';
    btn.classList.remove('pipeline-controls__action-btn--start');
    btn.classList.add('pipeline-controls__action-btn--stop');
  } else if (validationStatus === 'active') {
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
export function renderStatusBadge(badge: HTMLElement, pipelineStatus: PipelineStatus | null): void {
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
export function renderFps(indicator: HTMLElement, pipelineStatus: PipelineStatus | null, fps: number): void {
  if (!pipelineStatus || pipelineStatus.status !== 'running') {
    indicator.textContent = '';
    return;
  }
  if (fps <= 0) {
    indicator.textContent = '';
    return;
  }
  indicator.textContent = `${fps.toFixed(1)} FPS`;
}
