import { Chart, Interaction } from 'chart.js/auto';
import zoomPlugin from 'chartjs-plugin-zoom';
import { crosshairPlugin } from './crosshair-plugin';
import { engineEventsPlugin } from './engine-events-plugin';
import { nearestPerDataset, NEAREST_PER_DATASET } from './interaction-mode';

Chart.register(zoomPlugin);
Chart.register(crosshairPlugin);
Chart.register(engineEventsPlugin);
// Hovering lists every flight at the hovered time, not just the nearest one
Interaction.modes[NEAREST_PER_DATASET] = nearestPerDataset;

export { TimeSeriesGraphs } from './graphs';
