import { SERIES_OPTIONS } from './panels';
import type { SeriesKey } from './types';
import { ExportMenu } from './export/export-menu';
import type { ExportOptions } from './export/export-png';

/** What the toolbar's controls do. */
export interface GraphsHandlers {
  onSelect(key: SeriesKey): void;
  onResetZoom(): void;
  onSave(options: ExportOptions): void;
}

/** The elements the graphs component keeps a handle on. */
export interface GraphsDom {
  selectElement: HTMLSelectElement;
  compareContainer: HTMLElement;
  canvasContainer: HTMLElement;
  loadingOverlay: HTMLElement;
}

/** Build the header, compare toolbar and canvas container inside `container`. */
export function renderGraphsDom(
  container: HTMLElement,
  selectedKey: SeriesKey,
  handlers: GraphsHandlers,
): GraphsDom {
  container.innerHTML = '';

  const wrapper = document.createElement('div');
  wrapper.className = 'time-series-graphs';

  // Row 1: Title + primary controls (select + reset)
  const header = document.createElement('div');
  header.className = 'time-series-graphs__header';

  const title = document.createElement('h2');
  title.className = 'time-series-graphs__heading';
  title.textContent = 'Time-Series';

  const controls = document.createElement('div');
  controls.className = 'time-series-graphs__controls';

  const selectElement = document.createElement('select');
  selectElement.className = 'time-series-graphs__select';
  selectElement.setAttribute('aria-label', 'Select data series');

  for (const option of SERIES_OPTIONS) {
    const opt = document.createElement('option');
    opt.value = option.key;
    opt.textContent = option.label;
    selectElement.appendChild(opt);
  }

  selectElement.value = selectedKey;
  selectElement.addEventListener('change', () => handlers.onSelect(selectElement.value as SeriesKey));

  const resetBtn = document.createElement('button');
  resetBtn.className = 'time-series-graphs__reset-btn';
  resetBtn.textContent = 'Reset Zoom';
  resetBtn.setAttribute('aria-label', 'Reset zoom');
  resetBtn.addEventListener('click', () => handlers.onResetZoom());

  const exportMenu = new ExportMenu((options) => handlers.onSave(options));

  controls.appendChild(selectElement);
  controls.appendChild(resetBtn);
  controls.appendChild(exportMenu.element);

  header.appendChild(title);
  header.appendChild(controls);

  // Row 2: Compare flights + zoom hint
  const toolbar = document.createElement('div');
  toolbar.className = 'time-series-graphs__toolbar';

  const compareWrapper = document.createElement('div');
  compareWrapper.className = 'time-series-graphs__compare-wrapper';

  const compareLabel = document.createElement('span');
  compareLabel.className = 'time-series-graphs__compare-label';
  compareLabel.textContent = 'Compare:';
  compareWrapper.appendChild(compareLabel);

  const compareContainer = document.createElement('div');
  compareContainer.className = 'time-series-graphs__compare-options';
  compareWrapper.appendChild(compareContainer);

  const zoomHint = document.createElement('span');
  zoomHint.className = 'time-series-graphs__zoom-hint';
  zoomHint.textContent = 'Drag to pan · Ctrl+Drag to zoom · Scroll to zoom';

  toolbar.appendChild(compareWrapper);
  toolbar.appendChild(zoomHint);

  // Canvas container
  const canvasContainer = document.createElement('div');
  canvasContainer.className = 'time-series-graphs__canvas-container';

  const canvas = document.createElement('canvas');
  canvas.id = 'chart-main';
  canvasContainer.appendChild(canvas);

  // Loading overlay shown while comparison flights are being fetched
  const loadingOverlay = document.createElement('div');
  loadingOverlay.className = 'time-series-graphs__loading';
  loadingOverlay.setAttribute('role', 'status');
  loadingOverlay.hidden = true;

  const spinner = document.createElement('span');
  spinner.className = 'time-series-graphs__spinner';
  spinner.setAttribute('aria-hidden', 'true');

  const loadingText = document.createElement('span');
  loadingText.className = 'time-series-graphs__loading-text';
  loadingText.textContent = 'Loading comparison data…';

  loadingOverlay.appendChild(spinner);
  loadingOverlay.appendChild(loadingText);
  canvasContainer.appendChild(loadingOverlay);

  wrapper.appendChild(header);
  wrapper.appendChild(toolbar);
  wrapper.appendChild(canvasContainer);
  container.appendChild(wrapper);

  return { selectElement, compareContainer, canvasContainer, loadingOverlay };
}
