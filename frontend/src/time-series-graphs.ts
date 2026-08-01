import { Chart, ChartConfiguration, ChartData } from 'chart.js/auto';
import zoomPlugin from 'chartjs-plugin-zoom';
import type { StateManager, TimeSeriesPoint, TimeSeriesStore } from './state';

Chart.register(zoomPlugin);

interface GraphConfig {
  id: string;
  title: string;
  dataKey: 'speedSuperHeavy' | 'speedStarship' | 'altitudeSuperHeavy' | 'altitudeStarship';
  yLabel: string;
  lineColor: string;
}

const GRAPH_CONFIGS: GraphConfig[] = [
  {
    id: 'speed-super-heavy',
    title: 'Speed — Super Heavy',
    dataKey: 'speedSuperHeavy',
    yLabel: 'Speed',
    lineColor: '#FF8014',
  },
  {
    id: 'speed-starship',
    title: 'Speed — Starship',
    dataKey: 'speedStarship',
    yLabel: 'Speed',
    lineColor: '#4caf50',
  },
  {
    id: 'altitude-super-heavy',
    title: 'Altitude — Super Heavy',
    dataKey: 'altitudeSuperHeavy',
    yLabel: 'Altitude',
    lineColor: '#FF8014',
  },
  {
    id: 'altitude-starship',
    title: 'Altitude — Starship',
    dataKey: 'altitudeStarship',
    yLabel: 'Altitude',
    lineColor: '#4caf50',
  },
];

/**
 * Time-Series Graphs component.
 * Renders 4 interactive line charts (speed/altitude per vehicle)
 * using Chart.js with zoom/pan plugin.
 */
export class TimeSeriesGraphs {
  private charts: Map<string, Chart<'line'>> = new Map();
  private container: HTMLElement;
  private stateManager: StateManager;

  constructor(container: HTMLElement, stateManager: StateManager) {
    this.container = container;
    this.stateManager = stateManager;
    this.render();
    this.initCharts();
    this.stateManager.subscribe((state) => {
      this.updateCharts(state.timeSeries);
    });
  }

  private render(): void {
    this.container.innerHTML = `
      <div class="time-series-graphs">
        <h2 class="time-series-graphs__heading">Time-Series Graphs</h2>
        <div class="time-series-graphs__grid">
          ${GRAPH_CONFIGS.map(
            (config) => `
            <div class="time-series-graphs__chart-wrapper" data-graph-id="${config.id}">
              <div class="time-series-graphs__chart-header">
                <span class="time-series-graphs__chart-title">${config.title}</span>
                <button class="time-series-graphs__reset-btn" data-reset-for="${config.id}" aria-label="Reset zoom for ${config.title}">Reset Zoom</button>
              </div>
              <div class="time-series-graphs__canvas-container">
                <canvas id="chart-${config.id}"></canvas>
              </div>
            </div>
          `
          ).join('')}
        </div>
      </div>
    `;

    // Attach reset button handlers
    const resetButtons = this.container.querySelectorAll('.time-series-graphs__reset-btn');
    resetButtons.forEach((btn) => {
      btn.addEventListener('click', () => {
        const chartId = (btn as HTMLElement).dataset.resetFor;
        if (chartId) {
          const chart = this.charts.get(chartId);
          if (chart) {
            chart.resetZoom();
          }
        }
      });
    });
  }

  private initCharts(): void {
    for (const config of GRAPH_CONFIGS) {
      const canvas = this.container.querySelector(`#chart-${config.id}`) as HTMLCanvasElement | null;
      if (!canvas) continue;

      const chartConfig: ChartConfiguration<'line'> = {
        type: 'line',
        data: {
          labels: [],
          datasets: [
            {
              label: config.title,
              data: [],
              borderColor: config.lineColor,
              backgroundColor: `${config.lineColor}33`,
              borderWidth: 1.5,
              pointRadius: 0,
              pointHoverRadius: 4,
              tension: 0.2,
              fill: true,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: {
            mode: 'index',
            intersect: false,
          },
          scales: {
            x: {
              title: {
                display: true,
                text: 'Mission Elapsed Time',
                color: '#999999',
                font: { family: "'JetBrains Mono', monospace", size: 11 },
              },
              ticks: {
                color: '#999999',
                font: { family: "'JetBrains Mono', monospace", size: 10 },
                maxTicksLimit: 10,
              },
              grid: {
                color: '#444444',
              },
            },
            y: {
              title: {
                display: true,
                text: config.yLabel,
                color: '#999999',
                font: { family: "'JetBrains Mono', monospace", size: 11 },
              },
              ticks: {
                color: '#999999',
                font: { family: "'JetBrains Mono', monospace", size: 10 },
              },
              grid: {
                color: '#444444',
              },
            },
          },
          plugins: {
            legend: {
              display: false,
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

      const chart = new Chart(canvas, chartConfig) as Chart<'line'>;
      this.charts.set(config.id, chart);
    }
  }

  private updateCharts(timeSeries: TimeSeriesStore): void {
    for (const config of GRAPH_CONFIGS) {
      const chart = this.charts.get(config.id);
      if (!chart) continue;

      const points: TimeSeriesPoint[] = timeSeries[config.dataKey] ?? [];
      const labels = points.map((p) => p.missionElapsedTime);
      const values = points.map((p) => p.value);

      const data: ChartData<'line'> = {
        labels,
        datasets: [
          {
            ...chart.data.datasets[0],
            data: values,
          },
        ],
      };

      chart.data = data;
      chart.update('none');
    }
  }
}
