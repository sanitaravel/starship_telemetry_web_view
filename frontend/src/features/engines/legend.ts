import type { EngineStatus } from '../../domain/engines/events';
import { getStatusFill, getStatusStroke } from './diagram';

/**
 * Create the legend showing what each engine status color means.
 */
export function createLegend(): HTMLElement {
  const legend = document.createElement('div');
  legend.className = 'engine-visualizer__legend';
  legend.setAttribute('aria-label', 'Engine status legend');

  const items: { status: EngineStatus; label: string; description: string }[] = [
    { status: 'active', label: 'Active', description: 'Engine is firing' },
    { status: 'inactive', label: 'Inactive', description: 'Engine is off' },
    { status: 'undetected', label: 'Undetected', description: 'Status unknown' },
  ];

  for (const item of items) {
    const entry = document.createElement('div');
    entry.className = 'engine-visualizer__legend-item';

    const swatch = document.createElement('span');
    swatch.className = 'engine-visualizer__legend-swatch';
    swatch.style.backgroundColor = getStatusFill(item.status);
    swatch.style.borderColor = getStatusStroke(item.status);

    const label = document.createElement('span');
    label.className = 'engine-visualizer__legend-label';
    label.textContent = item.label;
    label.title = item.description;

    entry.appendChild(swatch);
    entry.appendChild(label);
    legend.appendChild(entry);
  }

  return legend;
}
