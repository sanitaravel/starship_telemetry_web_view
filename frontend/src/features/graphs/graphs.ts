import { Chart, ChartConfiguration } from 'chart.js/auto';
import type { StateManager } from '../../state/state-manager';
import type { TimeSeriesStore } from '../../core/time-series';
import type { EngineEvent } from '../../domain/engines/events';
import type { SeriesKey, SeriesOption } from './types';
import { SERIES_OPTIONS } from './panels';
import type { ExportOptions } from './export/export-png';
import { saveChartAsPng } from './export/save-chart';
import { seriesVehicle, type EngineMarker } from './plugins/engine-markers';
import { timelineMarkers } from './plugins/timeline-markers';
import type { MissionTimeline } from '../../state/mission-timeline';
import { createChartConfig } from './chart-config';
import { buildDatasets, buildMarkers, ChartData } from './chart-data';
import { Comparisons } from './comparisons';
import { renderGraphsDom } from './toolbar';
import { buildOverlayUrl, type OverlaySnapshot } from '../../domain/overlay/snapshot';
import { GRAPH_SERIES, graphWidgetId, overlayWidget } from '../../domain/overlay/widgets';
import { overlayBaseUrl, toggleObsPopover } from '../../ui/obs-popover';

export interface TimeSeriesGraphsOptions {
  /**
   * False draws the chart only (overlays): no header, toolbar, compare list
   * or loading overlay, and no zoom, pan or crosshair. Default true.
   */
  chrome?: boolean;
  /** The series shown first. Default speed of Super Heavy. */
  series?: SeriesKey;
  /** Compared flights as [filename, colour], fixed and headless (overlays). */
  comparisons?: readonly [string, string][];
  /**
   * The mission timeline: its events from T+0 on are drawn as dashed lines
   * in their status colour, next to the engine markers. Default none.
   */
  timeline?: MissionTimeline;
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
  private selectedKey: SeriesKey = 'speedSuperHeavy';
  private canvasContainer!: HTMLElement;
  private loadingOverlay!: HTMLElement;
  private latestTimeSeries: TimeSeriesStore | null = null;
  /** timeSeriesVersion of the store last drawn; -1 before the first update. */
  private renderedVersion = -1;
  /** Whether a coalesced live update is waiting for the next animation frame. */
  private frameRequested = false;
  /** Chart data of the live flight and the comparison flights. */
  private data = new ChartData();
  /** Live engine events (grows in place) and the engineEventsVersion last drawn. */
  private latestEngineEvents: EngineEvent[] = [];
  private renderedEventsVersion = 0;
  /** Live stage separation MET, null until known. */
  private latestSeparationMet: number | null = null;
  /** Engine event markers last drawn, also used by the PNG export. */
  private markers: EngineMarker[] = [];
  /** Config the on-screen chart was built from; its markers are updated in place. */
  private liveConfig: ChartConfiguration<'line'> | null = null;
  /** Previous flights offered for comparison and the ones overlaid. */
  private comparisons!: Comparisons;
  private readonly chrome: boolean;
  private readonly fixedComparisons: readonly [string, string][] | null;
  private readonly timeline: MissionTimeline | null;

  constructor(container: HTMLElement, stateManager: StateManager, options: TimeSeriesGraphsOptions = {}) {
    this.container = container;
    this.stateManager = stateManager;
    this.chrome = options.chrome ?? true;
    this.selectedKey = options.series ?? this.selectedKey;
    this.fixedComparisons = options.comparisons ?? null;
    this.timeline = options.timeline ?? null;
    this.render();
    this.initChart();
    this.comparisons.start();
    // A new event list or status redraws the markers
    this.timeline?.subscribe(() => this.scheduleUpdate());
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
    const dom = renderGraphsDom(this.container, this.selectedKey, {
      onSelect: (key) => {
        this.selectedKey = key;
        this.rebuildChart();
        this.redraw();
      },
      onResetZoom: () => {
        if (this.chart) {
          this.chart.resetZoom();
        }
      },
      onSave: (options) => this.saveAsPng(options),
      onObs: (button) => this.toggleObs(button),
    }, this.chrome);
    this.canvasContainer = dom.canvasContainer;
    this.loadingOverlay = dom.loadingOverlay;
    this.comparisons = new Comparisons(
      dom.compareContainer,
      {
        refresh: () => this.redraw(),
        // An overlay never shows the loading text on stream
        setLoading: (loading) => {
          if (this.chrome) this.setLoading(loading);
        },
      },
      this.fixedComparisons,
    );
  }

  /** The graph overlays' settings: the compared flights in load order with their colours. */
  overlaySnapshot(): OverlaySnapshot {
    return {
      v: 1,
      x: this.comparisons.loaded.map((c): [string, string] => [c.filename, this.comparisons.flightColor(c.filename)]),
    };
  }

  /** The OBS popover offering every series, the one shown preselected. */
  private toggleObs(button: HTMLButtonElement): void {
    toggleObsPopover(button, {
      title: 'Time-Series',
      widgets: GRAPH_SERIES.map(({ key }) => overlayWidget(graphWidgetId(key))),
      selected: graphWidgetId(this.selectedKey),
      urlFor: (widget) => buildOverlayUrl(overlayBaseUrl(), widget, this.overlaySnapshot()),
    });
  }

  private getSelectedOption(): SeriesOption {
    return SERIES_OPTIONS.find((o) => o.key === this.selectedKey) ?? SERIES_OPTIONS[0];
  }

  private initChart(): void {
    const canvas = this.container.querySelector('#chart-main') as HTMLCanvasElement | null;
    if (!canvas) return;

    this.liveConfig = createChartConfig(this.getSelectedOption(), this.markers, undefined, this.chrome);
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
   * Redraw on the next animation frame. Telemetry can arrive faster than the
   * display refreshes; this draws at most once per frame with the latest data.
   */
  private scheduleUpdate(): void {
    if (typeof requestAnimationFrame !== 'function') {
      this.redraw();
      return;
    }
    if (this.frameRequested) return;
    this.frameRequested = true;
    requestAnimationFrame(() => {
      this.frameRequested = false;
      this.redraw();
    });
  }

  private static readonly EMPTY_STORE: TimeSeriesStore = {
    speedSuperHeavy: [],
    speedStarship: [],
    altitudeSuperHeavy: [],
    altitudeStarship: [],
  };

  /** Draw the latest store (an empty one if there is no live data yet). */
  private redraw(): void {
    this.updateChart(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
  }

  private updateChart(timeSeries: TimeSeriesStore): void {
    if (!this.chart) return;

    const option = this.getSelectedOption();
    const liveXY = this.data.live(option, timeSeries);
    const comparisons = this.comparisons.loaded;
    const colorOf = (filename: string) => this.comparisons.flightColor(filename);

    const datasets = buildDatasets(
      option,
      liveXY,
      comparisons,
      (comp) => this.data.comparison(option, comp),
      colorOf,
    );

    const markers = buildMarkers(option, this.latestEngineEvents, this.latestSeparationMet, comparisons, colorOf);
    if (this.timeline) {
      const statuses = this.timeline.statuses;
      markers.push(
        ...timelineMarkers(
          this.timeline.events,
          (id) => statuses.status(id),
          seriesVehicle(option.derive?.from ?? option.key),
          this.latestSeparationMet,
        ),
      );
    }
    this.setMarkers(markers);
    this.chart.data = { datasets };
    this.chart.update('none');
  }

  /** Hand markers to the engineEvents plugin of the on-screen chart. */
  private setMarkers(markers: EngineMarker[]): void {
    this.markers = markers;
    const plugins = this.liveConfig?.options?.plugins;
    if (plugins) plugins.engineEvents = { markers };
  }

  /** Download the chart as currently shown as a PNG (see saveChartAsPng). */
  private saveAsPng(options: ExportOptions): void {
    const live = this.chart;
    if (!live) return;
    saveChartAsPng(live, this.selectedKey, options, (style) =>
      createChartConfig(this.getSelectedOption(), this.markers, style),
    );
  }

  private setLoading(loading: boolean): void {
    this.loadingOverlay.hidden = !loading;
    this.canvasContainer.setAttribute('aria-busy', String(loading));
  }
}
