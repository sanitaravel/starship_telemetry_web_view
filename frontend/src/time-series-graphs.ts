import { Chart, ChartConfiguration } from 'chart.js/auto';
import zoomPlugin from 'chartjs-plugin-zoom';
import type { StateManager, TimeSeriesPoint, TimeSeriesStore } from './state';
import { fetchPreviousFlightList, fetchPreviousFlightData, PreviousFlightInfo } from './previous-flights';

Chart.register(zoomPlugin);

/**
 * Custom Chart.js plugin that draws a vertical crosshair line at the mouse position.
 */
const crosshairPlugin = {
  id: 'crosshair',
  afterDraw(chart: Chart) {
    const tooltip = chart.tooltip;
    if (!tooltip || !tooltip.opacity) return;

    const ctx = chart.ctx;
    const x = tooltip.caretX;
    const topY = chart.scales.y.top;
    const bottomY = chart.scales.y.bottom;

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, topY);
    ctx.lineTo(x, bottomY);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.restore();
  },
};

Chart.register(crosshairPlugin);

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
    lineColor: '#FF8014',
  },
  {
    key: 'altitudeSuperHeavy',
    label: 'Altitude — Super Heavy',
    yLabel: 'Altitude',
    lineColor: '#FF8014',
  },
  {
    key: 'altitudeStarship',
    label: 'Altitude — Starship',
    yLabel: 'Altitude',
    lineColor: '#FF8014',
  },
];

/** Colors for comparison flight overlays */
const COMPARE_COLORS = [
  '#e91e63',
  '#00bcd4',
  '#ffeb3b',
  '#9c27b0',
  '#ff5722',
  '#607d8b',
];

/**
 * Parse a MET string like "+00:01:23" or "-00:00:05" into seconds from T-0.
 */
function parseMETToSeconds(met: string): number {
  const sign = met.startsWith('-') ? -1 : 1;
  const stripped = met.replace(/^[+\-T]/, '');
  const parts = stripped.split(':');
  const hours = parseInt(parts[0], 10) || 0;
  const minutes = parseInt(parts[1], 10) || 0;
  const seconds = parseInt(parts[2], 10) || 0;
  return sign * (hours * 3600 + minutes * 60 + seconds);
}

/**
 * Format seconds into a MET display string for axis ticks.
 */
function formatSecondsToMET(totalSeconds: number): string {
  const sign = totalSeconds < 0 ? '-' : '+';
  const abs = Math.abs(Math.round(totalSeconds));
  const h = Math.floor(abs / 3600);
  const m = Math.floor((abs % 3600) / 60);
  const s = abs % 60;
  if (h > 0) {
    return `${sign}${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  return `${sign}${m}:${String(s).padStart(2, '0')}`;
}

/**
 * Sort points by x-value, deduplicate (first value per x wins), and insert
 * NaN gap markers where consecutive points are more than GAP_THRESHOLD_SECONDS apart.
 * This causes Chart.js to break the line at data gaps.
 */
const GAP_THRESHOLD_SECONDS = 5;

function deduplicateByX(points: { x: number; y: number }[]): { x: number; y: number | null }[] {
  if (points.length === 0) return [];

  // Use a Map to keep the first y value for each x
  const map = new Map<number, number>();
  for (const p of points) {
    if (!map.has(p.x)) {
      map.set(p.x, p.y);
    }
  }

  // Convert back to sorted array
  const sorted: { x: number; y: number }[] = [];
  for (const [x, y] of map) {
    sorted.push({ x, y });
  }
  sorted.sort((a, b) => a.x - b.x);

  // Insert NaN gap markers between points that are too far apart
  const result: { x: number; y: number | null }[] = [];
  for (let i = 0; i < sorted.length; i++) {
    if (i > 0 && sorted[i].x - sorted[i - 1].x > GAP_THRESHOLD_SECONDS) {
      // Insert a gap point midway to break the line
      result.push({ x: (sorted[i - 1].x + sorted[i].x) / 2, y: null });
    }
    result.push(sorted[i]);
  }
  return result;
}

interface LoadedComparison {
  filename: string;
  name: string;
  data: TimeSeriesStore;
}

/**
 * Time-Series Graphs component.
 * Renders a single interactive line chart with a dropdown to select
 * which data series to display. Supports Chart.js zoom/pan.
 * Overlays one or more previous flights for comparison using a shared
 * numeric time axis (seconds from T-0).
 */
export class TimeSeriesGraphs {
  private chart: Chart<'line'> | null = null;
  private container: HTMLElement;
  private stateManager: StateManager;
  private selectedKey: DatasetKey = 'speedSuperHeavy';
  private selectElement!: HTMLSelectElement;
  private compareContainer!: HTMLElement;
  private latestTimeSeries: TimeSeriesStore | null = null;

  // Previous flight comparison state
  private previousFlights: PreviousFlightInfo[] = [];
  private selectedCompareFilenames: Set<string> = new Set();
  private loadedComparisons: LoadedComparison[] = [];

  constructor(container: HTMLElement, stateManager: StateManager) {
    this.container = container;
    this.stateManager = stateManager;
    this.render();
    this.initChart();
    this.loadPreviousFlightList();
    this.stateManager.subscribe((state) => {
      this.latestTimeSeries = state.timeSeries;
      this.updateChart(state.timeSeries);
    });
  }

  private render(): void {
    this.container.innerHTML = '';

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
      this.updateChart(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
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

    // Row 2: Compare flights + zoom hint
    const toolbar = document.createElement('div');
    toolbar.className = 'time-series-graphs__toolbar';

    const compareWrapper = document.createElement('div');
    compareWrapper.className = 'time-series-graphs__compare-wrapper';

    const compareLabel = document.createElement('span');
    compareLabel.className = 'time-series-graphs__compare-label';
    compareLabel.textContent = 'Compare:';
    compareWrapper.appendChild(compareLabel);

    this.compareContainer = document.createElement('div');
    this.compareContainer.className = 'time-series-graphs__compare-options';
    compareWrapper.appendChild(this.compareContainer);

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

    wrapper.appendChild(header);
    wrapper.appendChild(toolbar);
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
        datasets: [],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: {
          mode: 'nearest',
          axis: 'x',
          intersect: false,
        },
        scales: {
          x: {
            type: 'linear',
            title: {
              display: true,
              text: 'Mission Elapsed Time (T+seconds)',
              color: '#999999',
              font: { family: "'JetBrains Mono', monospace", size: 11 },
            },
            ticks: {
              color: '#999999',
              font: { family: "'JetBrains Mono', monospace", size: 10 },
              maxTicksLimit: 12,
              callback: (value) => formatSecondsToMET(value as number),
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
          tooltip: {
            callbacks: {
              title: (items) => {
                if (items.length > 0 && items[0].parsed.x != null) {
                  return formatSecondsToMET(items[0].parsed.x);
                }
                return '';
              },
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

  /**
   * Convert live TimeSeriesPoints to {x, y} scatter data using MET string → seconds.
   * Sorted by x and deduplicated (last value wins) to prevent line looping.
   */
  private livePointsToXY(points: TimeSeriesPoint[]): { x: number; y: number | null }[] {
    return deduplicateByX(
      points.map((p) => ({
        x: parseMETToSeconds(p.missionElapsedTime),
        y: p.value,
      })),
    );
  }

  /**
   * Convert previous flight TimeSeriesPoints to {x, y} scatter data.
   * Uses parseMETToSeconds for a consistent time axis with live data.
   * Sorted by x and deduplicated to prevent line looping.
   */
  private compPointsToXY(points: TimeSeriesPoint[]): { x: number; y: number | null }[] {
    return deduplicateByX(
      points.map((p) => ({
        x: parseMETToSeconds(p.missionElapsedTime),
        y: p.value,
      })),
    );
  }

  private static readonly EMPTY_STORE: TimeSeriesStore = {
    speedSuperHeavy: [],
    speedStarship: [],
    altitudeSuperHeavy: [],
    altitudeStarship: [],
  };

  private updateChart(timeSeries: TimeSeriesStore): void {
    if (!this.chart) return;

    const option = this.getSelectedOption();
    const points: TimeSeriesPoint[] = timeSeries[this.selectedKey] ?? [];
    const liveXY = this.livePointsToXY(points);

    const datasets: any[] = [];

    // Only include the live dataset if there's actual data
    if (liveXY.length > 0) {
      datasets.push({
        label: option.label,
        data: liveXY,
        borderColor: option.lineColor,
        backgroundColor: `${option.lineColor}22`,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.2,
        fill: true,
        spanGaps: false,
      });
    }

    // Add each comparison flight as an overlay dataset
    for (let i = 0; i < this.loadedComparisons.length; i++) {
      const comp = this.loadedComparisons[i];
      const compPoints: TimeSeriesPoint[] = comp.data[this.selectedKey] ?? [];
      const compXY = this.compPointsToXY(compPoints);
      // Use color matching the checkbox swatch (by position in previousFlights list)
      const flightIndex = this.previousFlights.findIndex((f) => f.filename === comp.filename);
      const color = COMPARE_COLORS[(flightIndex >= 0 ? flightIndex : i) % COMPARE_COLORS.length];

      datasets.push({
        label: `${option.label} (${comp.name})`,
        data: compXY,
        borderColor: color,
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        borderDash: [6, 3],
        pointRadius: 0,
        pointHoverRadius: 3,
        tension: 0.2,
        fill: false,
        spanGaps: false,
      });
    }

    this.chart.data = { datasets };
    this.chart.update('none');
  }

  /**
   * Load the list of available previous flights and populate the compare checkboxes.
   */
  private async loadPreviousFlightList(): Promise<void> {
    this.previousFlights = await fetchPreviousFlightList();

    for (let i = 0; i < this.previousFlights.length; i++) {
      const flight = this.previousFlights[i];
      const color = COMPARE_COLORS[i % COMPARE_COLORS.length];

      const label = document.createElement('label');
      label.className = 'time-series-graphs__compare-item';

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = flight.filename;
      checkbox.className = 'time-series-graphs__compare-checkbox';
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
          this.selectedCompareFilenames.add(flight.filename);
        } else {
          this.selectedCompareFilenames.delete(flight.filename);
        }
        this.onCompareSelectionChange();
      });

      const swatch = document.createElement('span');
      swatch.className = 'time-series-graphs__compare-swatch';
      swatch.style.backgroundColor = color;

      const text = document.createElement('span');
      text.textContent = flight.name;

      label.appendChild(checkbox);
      label.appendChild(swatch);
      label.appendChild(text);
      this.compareContainer.appendChild(label);
    }
  }

  /**
   * Handle change on the checkboxes: load/unload comparison data as needed.
   */
  private async onCompareSelectionChange(): Promise<void> {
    const selectedFilenames = Array.from(this.selectedCompareFilenames);

    // Remove comparisons that are no longer selected
    this.loadedComparisons = this.loadedComparisons.filter(
      (c) => selectedFilenames.includes(c.filename),
    );

    // Load newly selected comparisons
    const alreadyLoaded = new Set(this.loadedComparisons.map((c) => c.filename));
    const toLoad = selectedFilenames.filter((f) => !alreadyLoaded.has(f));

    const loadPromises = toLoad.map(async (filename) => {
      const data = await fetchPreviousFlightData(filename);
      if (data) {
        const info = this.previousFlights.find((f) => f.filename === filename);
        this.loadedComparisons.push({
          filename,
          name: info?.name ?? filename,
          data,
        });
      }
    });

    await Promise.all(loadPromises);

    // Refresh chart (use empty store if no live data yet)
    this.updateChart(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
  }
}
