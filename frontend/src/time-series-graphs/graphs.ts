import { Chart, ChartConfiguration } from 'chart.js/auto';
import type { StateManager } from '../state/state-manager';
import type { TimeSeriesPoint, TimeSeriesStore } from '../core/time-series';
import { formatSecondsToMET } from '../core/met';
import type { EngineEvent } from '../domain/engines/events';
import type { PreviousFlightInfo } from '../core/types';
import { fetchPreviousFlightList, fetchPreviousFlightData, PreviousFlightData } from '../api/previous-flights';
import type { DatasetKey, LoadedComparison, SeriesKey, SeriesOption } from './types';
import { COMPARE_COLORS, SERIES_OPTIONS } from './panels';
import { NEAREST_PER_DATASET } from './interaction-mode';
import { computeAcceleration, IncrementalXY } from '../domain/series-math';
import {
  chartFilename,
  DEFAULT_EXPORT_OPTIONS,
  EXPORT_THEMES,
  saveCanvasAsPng,
  watermarkFontPx,
  type ChartTheme,
  type ExportOptions,
} from './export-png';
import { ExportMenu } from './export-menu';
import { eventMarkers, eventsForSeries, seriesVehicle, type EngineMarker } from './engine-markers';
import { createLogger } from '../core/logger';
import { FlightColors } from '../state/flight-colors';
import { createSwatchButton, toggleColorPopover } from '../color-picker';

const logger = createLogger('time-series-graphs');

let nextCompareId = 0;

type XY = { x: number; y: number | null };

/** Font size and colours used to build a chart config. */
interface ChartStyle {
  /** Tick label size in px; axis titles and the legend are 1 px larger. */
  fontSize: number;
  theme: ChartTheme;
}

/** The on-screen chart: default font size on the dark panel. */
const LIVE_STYLE: ChartStyle = {
  fontSize: DEFAULT_EXPORT_OPTIONS.fontSize,
  theme: EXPORT_THEMES.dark,
};

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
  private selectedKey: SeriesKey = 'speedSuperHeavy';
  private selectElement!: HTMLSelectElement;
  private compareContainer!: HTMLElement;
  private canvasContainer!: HTMLElement;
  private loadingOverlay!: HTMLElement;
  private latestTimeSeries: TimeSeriesStore | null = null;
  /** timeSeriesVersion of the store last drawn; -1 before the first update. */
  private renderedVersion = -1;
  /** Whether a coalesced live update is waiting for the next animation frame. */
  private frameRequested = false;
  /** The live series' chart data, extended in place as points arrive. */
  private liveXY = new IncrementalXY();
  /** Live engine events (grows in place) and the engineEventsVersion last drawn. */
  private latestEngineEvents: EngineEvent[] = [];
  private renderedEventsVersion = 0;
  /** Live stage separation MET, null until known. */
  private latestSeparationMet: number | null = null;
  /** Engine event markers last drawn, also used by the PNG export. */
  private markers: EngineMarker[] = [];
  /** Config the on-screen chart was built from; its markers are updated in place. */
  private liveConfig: ChartConfiguration<'line'> | null = null;

  // Previous flight comparison state
  private previousFlights: PreviousFlightInfo[] = [];
  private selectedCompareFilenames: Set<string> = new Set();
  private loadedComparisons: LoadedComparison[] = [];
  /** In-flight comparison fetches, keyed by filename. */
  private pendingLoads: Map<string, Promise<void>> = new Map();
  /** Fetched comparison flights, kept so re-selecting one needs no refetch. */
  private flightCache: Map<string, PreviousFlightData> = new Map();
  /** Chart data per comparison flight and series; flights never change. */
  private overlayXY: Map<string, Map<SeriesKey, XY[]>> = new Map();
  /** User-picked overlay colours, keyed by flight filename. */
  private colors = new FlightColors('telemetry');
  /** Swatch buttons in the compare list, keyed by flight filename. */
  private swatches: Map<string, HTMLButtonElement> = new Map();

  constructor(container: HTMLElement, stateManager: StateManager) {
    this.container = container;
    this.stateManager = stateManager;
    this.render();
    this.initChart();
    this.loadPreviousFlightList();
    this.colors.subscribe(() => this.onColorsChange());
    this.stateManager.subscribe((state) => {
      // Most messages (frames, status) leave the series and events untouched
      if (
        state.timeSeries === this.latestTimeSeries &&
        state.timeSeriesVersion === this.renderedVersion &&
        state.engineEvents === this.latestEngineEvents &&
        state.engineEventsVersion === this.renderedEventsVersion &&
        state.separationMetSeconds === this.latestSeparationMet
      ) {
        return;
      }
      this.latestTimeSeries = state.timeSeries;
      this.renderedVersion = state.timeSeriesVersion;
      this.latestEngineEvents = state.engineEvents;
      this.renderedEventsVersion = state.engineEventsVersion;
      this.latestSeparationMet = state.separationMetSeconds;
      this.scheduleUpdate();
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
      this.selectedKey = this.selectElement.value as SeriesKey;
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

    const exportMenu = new ExportMenu((options) => this.saveAsPng(options));

    controls.appendChild(this.selectElement);
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

    this.compareContainer = document.createElement('div');
    this.compareContainer.className = 'time-series-graphs__compare-options';
    compareWrapper.appendChild(this.compareContainer);

    const zoomHint = document.createElement('span');
    zoomHint.className = 'time-series-graphs__zoom-hint';
    zoomHint.textContent = 'Drag to pan · Ctrl+Drag to zoom · Scroll to zoom';

    toolbar.appendChild(compareWrapper);
    toolbar.appendChild(zoomHint);

    // Canvas container
    this.canvasContainer = document.createElement('div');
    this.canvasContainer.className = 'time-series-graphs__canvas-container';

    const canvas = document.createElement('canvas');
    canvas.id = 'chart-main';
    this.canvasContainer.appendChild(canvas);

    // Loading overlay shown while comparison flights are being fetched
    this.loadingOverlay = document.createElement('div');
    this.loadingOverlay.className = 'time-series-graphs__loading';
    this.loadingOverlay.setAttribute('role', 'status');
    this.loadingOverlay.hidden = true;

    const spinner = document.createElement('span');
    spinner.className = 'time-series-graphs__spinner';
    spinner.setAttribute('aria-hidden', 'true');

    const loadingText = document.createElement('span');
    loadingText.className = 'time-series-graphs__loading-text';
    loadingText.textContent = 'Loading comparison data…';

    this.loadingOverlay.appendChild(spinner);
    this.loadingOverlay.appendChild(loadingText);
    this.canvasContainer.appendChild(this.loadingOverlay);

    wrapper.appendChild(header);
    wrapper.appendChild(toolbar);
    wrapper.appendChild(this.canvasContainer);
    this.container.appendChild(wrapper);
  }

  private getSelectedOption(): SeriesOption {
    return SERIES_OPTIONS.find((o) => o.key === this.selectedKey) ?? SERIES_OPTIONS[0];
  }

  private createChartConfig(style: ChartStyle = LIVE_STYLE): ChartConfiguration<'line'> {
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
            markers: this.markers,
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

    this.liveConfig = this.createChartConfig();
    this.chart = new Chart(canvas, this.liveConfig) as Chart<'line'>;
  }

  private rebuildChart(): void {
    if (this.chart) {
      this.chart.destroy();
      this.chart = null;
    }
    this.initChart();
  }

  /**
   * Build the {x, y} data for an option from a store, in seconds from T-0
   * (sub-second where available), sorted and deduplicated so the line never
   * loops. Derived series (acceleration) come from their base speed series.
   * The live series is extended incrementally (see IncrementalXY).
   */
  private liveOptionXY(option: SeriesOption, store: TimeSeriesStore): XY[] {
    if (option.derive) {
      return computeAcceleration(store[option.derive.from] ?? []);
    }
    return this.liveXY.update(store[option.key as DatasetKey] ?? []);
  }

  /** Chart data for a comparison flight, computed once per flight and series. */
  private comparisonXY(option: SeriesOption, comp: LoadedComparison): XY[] {
    let perSeries = this.overlayXY.get(comp.filename);
    if (!perSeries) {
      perSeries = new Map();
      this.overlayXY.set(comp.filename, perSeries);
    }
    let xy = perSeries.get(option.key);
    if (!xy) {
      xy = option.derive
        ? computeAcceleration(comp.data[option.derive.from] ?? [])
        : new IncrementalXY().update(comp.data[option.key as DatasetKey] ?? []);
      perSeries.set(option.key, xy);
    }
    return xy;
  }

  /**
   * Redraw on the next animation frame. Telemetry can arrive faster than the
   * display refreshes; this draws at most once per frame with the latest data.
   */
  private scheduleUpdate(): void {
    if (typeof requestAnimationFrame !== 'function') {
      this.updateChart(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
      return;
    }
    if (this.frameRequested) return;
    this.frameRequested = true;
    requestAnimationFrame(() => {
      this.frameRequested = false;
      this.updateChart(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
    });
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
    const liveXY = this.liveOptionXY(option, timeSeries);

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
        // Acceleration swings above and below zero, so skip the area fill.
        fill: !option.derive,
        spanGaps: false,
        // Data is already {x, y}, sorted by x and unique, so Chart.js can
        // skip parsing and its own sorting/uniqueness checks.
        parsing: false,
        normalized: true,
      });
    }

    // Add each comparison flight as an overlay dataset
    for (let i = 0; i < this.loadedComparisons.length; i++) {
      const comp = this.loadedComparisons[i];
      const compXY = this.comparisonXY(option, comp);
      const color = this.flightColor(comp.filename);

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
        parsing: false,
        normalized: true,
      });
    }

    this.setMarkers(this.buildMarkers(option));
    this.chart.data = { datasets };
    this.chart.update('none');
  }

  /**
   * Engine event markers for a series: the live flight's in red/amber, each
   * comparison flight's in its overlay colour (see eventsForSeries).
   */
  private buildMarkers(option: SeriesOption): EngineMarker[] {
    const vehicle = seriesVehicle(option.derive?.from ?? option.key);
    const markers = eventMarkers(
      eventsForSeries(this.latestEngineEvents ?? [], vehicle, this.latestSeparationMet ?? null),
    );
    for (const comp of this.loadedComparisons) {
      markers.push(
        ...eventMarkers(
          eventsForSeries(comp.engineEvents, vehicle, comp.separationMetSeconds),
          this.flightColor(comp.filename),
          comp.name,
        ),
      );
    }
    return markers;
  }

  /** Hand markers to the engineEvents plugin of the on-screen chart. */
  private setMarkers(markers: EngineMarker[]): void {
    this.markers = markers;
    const plugins = this.liveConfig?.options?.plugins;
    if (plugins) plugins.engineEvents = { markers };
  }

  /** The user's colour for a previous flight, else its palette colour by list position. */
  private flightColor(filename: string): string {
    const index = Math.max(0, this.previousFlights.findIndex((f) => f.filename === filename));
    return this.colors.get(filename, COMPARE_COLORS[index % COMPARE_COLORS.length]);
  }

  /** Repaint swatches and overlays after a colour is picked or reset. */
  private onColorsChange(): void {
    for (const [filename, swatch] of this.swatches) {
      swatch.style.backgroundColor = this.flightColor(filename);
    }
    this.updateChart(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
  }

  /**
   * Load the list of available previous flights and populate the compare checkboxes.
   */
  private async loadPreviousFlightList(): Promise<void> {
    this.previousFlights = await fetchPreviousFlightList();

    for (let i = 0; i < this.previousFlights.length; i++) {
      const flight = this.previousFlights[i];
      const checkboxId = `time-series-graphs-compare-${nextCompareId++}`;

      const item = document.createElement('div');
      item.className = 'time-series-graphs__compare-item';

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.id = checkboxId;
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

      const swatch = createSwatchButton(
        'time-series-graphs__compare-swatch',
        flight.name,
        this.flightColor(flight.filename),
      );
      swatch.addEventListener('click', () =>
        toggleColorPopover(swatch, {
          title: flight.name,
          color: this.flightColor(flight.filename),
          isCustom: this.colors.isCustom(flight.filename),
          onPick: (hex) => this.colors.set(flight.filename, hex),
          onReset: () => this.colors.reset(flight.filename),
        }),
      );
      this.swatches.set(flight.filename, swatch);

      const label = document.createElement('label');
      label.htmlFor = checkboxId;
      label.textContent = flight.name;

      item.appendChild(checkbox);
      item.appendChild(swatch);
      item.appendChild(label);
      this.compareContainer.appendChild(item);
    }
  }

  /**
   * Handle change on the checkboxes: load/unload comparison data as needed.
   */
  private async onCompareSelectionChange(): Promise<void> {
    // Remove comparisons that are no longer selected
    this.loadedComparisons = this.loadedComparisons.filter(
      (c) => this.selectedCompareFilenames.has(c.filename),
    );

    // Add newly selected comparisons: cached ones right away, the rest by
    // starting a fetch (skipping ones already in flight)
    const alreadyLoaded = new Set(this.loadedComparisons.map((c) => c.filename));
    for (const filename of this.selectedCompareFilenames) {
      if (alreadyLoaded.has(filename)) continue;
      const cached = this.flightCache.get(filename);
      if (cached) {
        this.addComparison(filename, cached);
      } else if (!this.pendingLoads.has(filename)) {
        this.pendingLoads.set(filename, this.loadComparison(filename));
      }
    }

    // Refresh chart (use empty store if no live data yet) so removals show immediately
    this.updateChart(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);

    if (this.pendingLoads.size === 0) return;

    // Keep the loading overlay up until every fetch has settled, including
    // fetches started by later selection changes while we were waiting.
    this.setLoading(true);
    while (this.pendingLoads.size > 0) {
      await Promise.all(this.pendingLoads.values());
    }

    this.updateChart(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
    this.setLoading(false);
  }

  /**
   * Fetch one comparison flight and add it to the chart data if it is still
   * selected when the fetch completes. Never rejects.
   */
  private async loadComparison(filename: string): Promise<void> {
    try {
      const data = await fetchPreviousFlightData(filename);
      if (data) {
        this.flightCache.set(filename, data);
        if (this.selectedCompareFilenames.has(filename)) {
          this.addComparison(filename, data);
        }
      }
    } catch (err) {
      logger.error('Failed to load comparison flight', { filename, error: String(err) });
    } finally {
      this.pendingLoads.delete(filename);
    }
  }

  /** Add a comparison flight to the chart data unless it is already there. */
  private addComparison(filename: string, flight: PreviousFlightData): void {
    if (this.loadedComparisons.some((c) => c.filename === filename)) return;
    const info = this.previousFlights.find((f) => f.filename === filename);
    this.loadedComparisons.push({
      filename,
      name: info?.name ?? filename,
      data: flight.store,
      engineEvents: flight.engineEvents,
      separationMetSeconds: flight.separationMetSeconds,
    });
  }

  /**
   * Download the chart as currently shown (series, zoom/pan, comparison
   * overlays) as a PNG with the chosen font size and background.
   *
   * The export is drawn by a separate, off-screen Chart.js instance built
   * from the same config with the export style, sized like the live chart
   * and pinned to its current axis ranges, so the on-screen chart is never
   * restyled or redrawn.
   */
  private saveAsPng(options: ExportOptions): void {
    const live = this.chart;
    if (!live) return;

    const theme = EXPORT_THEMES[options.background];
    const pixelRatio = live.currentDevicePixelRatio || window.devicePixelRatio || 1;
    const config = this.createChartConfig({ fontSize: options.fontSize, theme });
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
        chartFilename(this.selectedKey),
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

  private setLoading(loading: boolean): void {
    this.loadingOverlay.hidden = !loading;
    this.canvasContainer.setAttribute('aria-busy', String(loading));
  }
}
