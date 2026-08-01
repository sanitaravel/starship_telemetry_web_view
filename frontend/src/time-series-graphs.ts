import { Chart, ChartConfiguration, ChartData } from 'chart.js/auto';
import zoomPlugin from 'chartjs-plugin-zoom';
import type { StateManager, TimeSeriesPoint, TimeSeriesStore } from './state';

Chart.register(zoomPlugin);

type DatasetKey = 'speedSuperHeavy' | 'speedStarship' | 'altitudeSuperHeavy' | 'altitudeStarship';

interface SeriesOption {
  key: DatasetKey;
  label: string;
  yLabel: string;
  lineColor: string;
}

const SERIES_OPTIONS: SeriesOption[] = [
  {
    key: 'speedSuperHeavy',
    label: 'Speed — Super Heavy',
    yLabel: 'Speed',
    lineColor: '#FF8014',
  },
  {
    key: 'speedStarship',
    label: 'Speed — Starship',
    yLabel: 'Speed',
    lineColor: '#4caf50',
  },
  {
    key: 'altitudeSuperHeavy',
    label: 'Altitude — Super Heavy',
    yLabel: 'Altitude',
    lineColor: '#2196f3',
  },
  {
    key: 'altitudeStarship',
    label: 'Altitude — Starship',
    yLabel: 'Altitude',
    lineColor: '#ab47bc',
  },
];

/**
 * Time-Series Graphs component.
 * Renders a single interactive line chart with a dropdown to select
 * which data series to display. Supports Chart.js zoom/pan.
 */
export class TimeSeriesGraphs {
  private chart: Chart<'line'> | null = null;
  private container: HTMLElement;
  private stateManager: StateManager;
  private selectedKey: DatasetKey = 'speedSuperHeavy';
  private selectElement!: HTMLSelectElement;
  private latestTimeSeries: TimeSeriesStore | null = null;

  constructor(container: HTMLElement, stateManager: StateManager) {
    this.container = container;
    this.stateManager = stateManager;
    this.render();
    this.initChart();
    this.stateManager.subscribe((state) => {
      this.latestTimeSeries = state.timeSeries;
      this.updateChart(state.timeSeries);
    });
  }

  private render(): void {
    this.container.innerHTML = '';

    const wrapper = document.createElement('div');
    wrapper.className = 'time-series-graphs';

    // Header row with title, dropdown, and reset button
    const header = document.createElement('div');
    header.className = 'time-series-graphs__header';

    const title = document.createElement('h2');
    title.className = 'time-series-graphs__heading';
    title.textContent = 'Time-Series';

    const controls = document.createElement('div');
    controls.className = 'time-series-graphs__controls';

    this.selectElement = document.createElement('select');
    this.selectElement.className = 'time-series-graphs__select';
    this.selectElement.setAttribute('aria-label', 'Select data series');

    for (const option of SERIES_OPTIONS) {
      const opt = document.createElement('option');
      opt.value = option.key;
      opt.textContent = option.label;
      this.selectElement.appendChild(opt);
    }

    this.selectElement.value = this.selectedKey;
    this.selectElement.addEventListener('change', () => {
      this.selectedKey = this.selectElement.value as DatasetKey;
      this.rebuildChart();
      if (this.latestTimeSeries) {
        this.updateChart(this.latestTimeSeries);
      }
    });

    const resetBtn = document.createElement('button');
    resetBtn.className = 'time-series-graphs__reset-btn';
    resetBtn.textContent = 'Reset Zoom';
    resetBtn.setAttribute('aria-label', 'Reset zoom');
    resetBtn.addEventListener('click', () => {
      if (this.chart) {
        this.chart.resetZoom();
      }
    });

    controls.appendChild(this.selectElement);
    controls.appendChild(resetBtn);

    header.appendChild(title);
    header.appendChild(controls);

    // Canvas container
    const canvasContainer = document.createElement('div');
    canvasContainer.className = 'time-series-graphs__canvas-container';

    const canvas = document.createElement('canvas');
    canvas.id = 'chart-main';
    canvasContainer.appendChild(canvas);

    wrapper.appendChild(header);
    wrapper.appendChild(canvasContainer);
    this.container.appendChild(wrapper);
  }

  private getSelectedOption(): SeriesOption {
    return SERIES_OPTIONS.find((o) => o.key === this.selectedKey) ?? SERIES_OPTIONS[0];
  }

  private createChartConfig(): ChartConfiguration<'line'> {
    const option = this.getSelectedOption();

    return {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: option.label,
            data: [],
            borderColor: option.lineColor,
            backgroundColor: `${option.lineColor}22`,
            borderWidth: 2,
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
              maxTicksLimit: 12,
            },
            grid: {
              color: '#444444',
            },
          },
          y: {
            title: {
              display: true,
              text: option.yLabel,
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
            display: true,
            labels: {
              color: '#999999',
              font: { family: "'JetBrains Mono', monospace", size: 11 },
              boxWidth: 12,
              boxHeight: 12,
            },
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
  }

  private initChart(): void {
    const canvas = this.container.querySelector('#chart-main') as HTMLCanvasElement | null;
    if (!canvas) return;

    this.chart = new Chart(canvas, this.createChartConfig()) as Chart<'line'>;
  }

  private rebuildChart(): void {
    if (this.chart) {
      this.chart.destroy();
      this.chart = null;
    }
    this.initChart();
  }

  private updateChart(timeSeries: TimeSeriesStore): void {
    if (!this.chart) return;

    const option = this.getSelectedOption();
    const points: TimeSeriesPoint[] = timeSeries[this.selectedKey] ?? [];
    const labels = points.map((p) => p.missionElapsedTime);
    const values = points.map((p) => p.value);

    const data: ChartData<'line'> = {
      labels,
      datasets: [
        {
          label: option.label,
          data: values,
          borderColor: option.lineColor,
          backgroundColor: `${option.lineColor}22`,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.2,
          fill: true,
        },
      ],
    };

    this.chart.data = data;
    this.chart.update('none');
  }
}
