import type { ChartConfiguration } from 'chart.js/auto';
import { formatSecondsToMET } from '../../core/met';
import type { SeriesOption } from './types';
import { NEAREST_PER_DATASET } from './plugins/interaction-mode';
import { DEFAULT_EXPORT_OPTIONS, EXPORT_THEMES, type ChartTheme } from './export/export-png';
import type { EngineMarker } from './plugins/engine-markers';

/** Font size and colours used to build a chart config. */
export interface ChartStyle {
  /** Tick label size in px; axis titles and the legend are 1 px larger. */
  fontSize: number;
  theme: ChartTheme;
}

/** The on-screen chart: default font size on the dark panel. */
export const LIVE_STYLE: ChartStyle = {
  fontSize: DEFAULT_EXPORT_OPTIONS.fontSize,
  theme: EXPORT_THEMES.dark,
};

/** Chart.js config for a series: axes, tooltip, zoom and the engine-event markers. */
export function createChartConfig(
  option: SeriesOption,
  markers: EngineMarker[],
  style: ChartStyle = LIVE_STYLE,
): ChartConfiguration<'line'> {
  return {
    type: 'line',
    data: {
      datasets: [],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: {
        // One point per flight at the hovered time (see interaction-mode.ts)
        mode: NEAREST_PER_DATASET,
        intersect: false,
      },
      scales: {
        x: {
          type: 'linear',
          title: {
            display: true,
            text: 'Mission Elapsed Time (T+seconds)',
            color: style.theme.text,
            font: { family: "'JetBrains Mono', monospace", size: style.fontSize + 1 },
          },
          ticks: {
            color: style.theme.text,
            font: { family: "'JetBrains Mono', monospace", size: style.fontSize },
            maxTicksLimit: 12,
            callback: (value) => formatSecondsToMET(value as number),
          },
          grid: {
            color: style.theme.grid,
          },
        },
        y: {
          title: {
            display: true,
            text: option.yLabel,
            color: style.theme.text,
            font: { family: "'JetBrains Mono', monospace", size: style.fontSize + 1 },
          },
          ticks: {
            color: style.theme.text,
            font: { family: "'JetBrains Mono', monospace", size: style.fontSize },
          },
          grid: {
            color: style.theme.grid,
          },
        },
      },
      plugins: {
        legend: {
          display: true,
          labels: {
            color: style.theme.text,
            font: { family: "'JetBrains Mono', monospace", size: style.fontSize + 1 },
            boxWidth: 12,
            boxHeight: 12,
          },
        },
        tooltip: {
          // Anchor the tooltip (and the crosshair) at the point nearest the
          // cursor rather than the average of all flights' points
          position: 'nearest',
          callbacks: {
            title: (items) => {
              if (items.length > 0 && items[0].parsed.x != null) {
                return formatSecondsToMET(items[0].parsed.x);
              }
              return '';
            },
          },
        },
        engineEvents: {
          markers,
        },
        zoom: {
          pan: {
            enabled: true,
            mode: 'x',
          },
          zoom: {
            wheel: {
              enabled: true,
            },
            drag: {
              enabled: true,
              modifierKey: 'ctrl',
              backgroundColor: 'rgba(255, 128, 20, 0.15)',
              borderColor: '#FF8014',
              borderWidth: 1,
            },
            mode: 'x',
          },
        },
      },
    },
  };
}
