/**
 * The interactive elements of the pipeline controls.
 */
export interface PipelineControlsElements {
  urlInput: HTMLInputElement;
  validateButton: HTMLButtonElement;
  validationIndicator: HTMLElement;
  actionButton: HTMLButtonElement;
  statusBadge: HTMLElement;
  intervalInput: HTMLInputElement;
  fpsIndicator: HTMLElement;
}

/**
 * Build the DOM structure for pipeline controls inside the container.
 */
export function renderPipelineControls(container: HTMLElement): PipelineControlsElements {
  container.innerHTML = '';
  container.classList.add('pipeline-controls');

  // URL input row
  const urlRow = document.createElement('div');
  urlRow.className = 'pipeline-controls__url-row';

  const urlInput = document.createElement('input');
  urlInput.type = 'url';
  urlInput.placeholder = 'Paste livestream URL...';
  urlInput.className = 'pipeline-controls__url-input';
  urlInput.setAttribute('aria-label', 'Livestream URL');

  const validateButton = document.createElement('button');
  validateButton.textContent = 'Validate';
  validateButton.className = 'pipeline-controls__validate-btn';
  validateButton.type = 'button';

  urlRow.appendChild(urlInput);
  urlRow.appendChild(validateButton);

  // Interval control row
  const intervalRow = document.createElement('div');
  intervalRow.className = 'pipeline-controls__interval-row';

  const intervalLabel = document.createElement('label');
  intervalLabel.className = 'pipeline-controls__interval-label';
  intervalLabel.textContent = 'Process every:';
  intervalLabel.setAttribute('for', 'interval-input');

  const intervalInput = document.createElement('input');
  intervalInput.type = 'number';
  intervalInput.id = 'interval-input';
  intervalInput.min = '1';
  intervalInput.max = '300';
  intervalInput.step = '1';
  intervalInput.value = '30';
  intervalInput.className = 'pipeline-controls__interval-input';
  intervalInput.setAttribute('aria-label', 'Process every Nth frame');

  const intervalUnit = document.createElement('span');
  intervalUnit.className = 'pipeline-controls__interval-unit';
  intervalUnit.textContent = 'th frame';

  intervalRow.appendChild(intervalLabel);
  intervalRow.appendChild(intervalInput);
  intervalRow.appendChild(intervalUnit);

  // Status row
  const statusRow = document.createElement('div');
  statusRow.className = 'pipeline-controls__status-row';

  const validationIndicator = document.createElement('span');
  validationIndicator.className = 'pipeline-controls__validation-status';
  validationIndicator.setAttribute('aria-live', 'polite');

  const actionButton = document.createElement('button');
  actionButton.className = 'pipeline-controls__action-btn';
  actionButton.type = 'button';
  actionButton.style.display = 'none';

  const statusBadge = document.createElement('span');
  statusBadge.className = 'pipeline-controls__status-badge';
  statusBadge.setAttribute('aria-live', 'polite');

  const fpsIndicator = document.createElement('span');
  fpsIndicator.className = 'pipeline-controls__fps';
  fpsIndicator.setAttribute('aria-live', 'polite');

  statusRow.appendChild(validationIndicator);
  statusRow.appendChild(actionButton);
  statusRow.appendChild(fpsIndicator);
  statusRow.appendChild(statusBadge);

  container.appendChild(urlRow);
  container.appendChild(intervalRow);
  container.appendChild(statusRow);

  return { urlInput, validateButton, validationIndicator, actionButton, statusBadge, intervalInput, fpsIndicator };
}
