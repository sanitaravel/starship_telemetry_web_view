import { Chart, ChartConfiguration } from 'chart.js/auto';
import type { SeriesKey } from '../types';
import type { ChartStyle } from '../chart-config';
import { chartFilename, EXPORT_THEMES, saveCanvasAsPng, watermarkFontPx, type ExportOptions } from './export-png';

/**
 * Download the chart as currently shown (series, zoom/pan, comparison
 * overlays) as a PNG with the chosen font size and background.
 *
 * The export is drawn by a separate, off-screen Chart.js instance built
 * from the same config with the export style, sized like the live chart
 * and pinned to its current axis ranges, so the on-screen chart is never
 * restyled or redrawn.
 */
export function saveChartAsPng(
  live: Chart<'line'>,
  key: SeriesKey,
  options: ExportOptions,
  createConfig: (style: ChartStyle) => ChartConfiguration<'line'>,
): void {
  const theme = EXPORT_THEMES[options.background];
  const pixelRatio = live.currentDevicePixelRatio || window.devicePixelRatio || 1;
  const config = createConfig({ fontSize: options.fontSize, theme });
  const chartOptions = config.options!;
  chartOptions.responsive = false;
  chartOptions.devicePixelRatio = pixelRatio;

  // Keep the current zoom/pan: pin both axes to the live chart's ranges
  const scales = chartOptions.scales as Record<'x' | 'y', { min?: number; max?: number }>;
  for (const axis of ['x', 'y'] as const) {
    const liveScale = live.scales[axis];
    if (liveScale) {
      scales[axis].min = liveScale.min;
      scales[axis].max = liveScale.max;
    }
  }

  // Copy the datasets so the export chart doesn't share arrays with the live one
  config.data.datasets = live.data.datasets.map((d) => ({ ...d, data: [...d.data] }));

  const canvas = document.createElement('canvas');
  canvas.width = live.width;
  canvas.height = live.height;
  const exportChart = new Chart(canvas, config);
  try {
    saveCanvasAsPng(
      exportChart.canvas,
      chartFilename(key),
      {
        fill: theme.fill,
        watermarkColor: theme.watermark,
        watermarkFontPx: watermarkFontPx(options.fontSize),
      },
      pixelRatio,
    );
  } finally {
    exportChart.destroy();
  }
}
