import { Chart } from 'chart.js/auto';
import zoomPlugin from 'chartjs-plugin-zoom';
import { crosshairPlugin } from './crosshair-plugin';

Chart.register(zoomPlugin);
Chart.register(crosshairPlugin);

export { TimeSeriesGraphs } from './graphs';
