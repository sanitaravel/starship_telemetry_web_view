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
/** Keys for series derived from the base store rather than stored directly. */
type DerivedKey = 'gForceSuperHeavy' | 'gForceStarship';
type SeriesKey = DatasetKey | DerivedKey;
type Vehicle = 'superHeavy' | 'starship';

interface SeriesOption {
  /** Unique identifier for this panel's series. */
  key: SeriesKey;
  label: string;
  yLabel: string;
  lineColor: string;
  /**
   * When set, the series is computed from the named base store series
   * instead of being read directly from the store.
   */
  derive?: { from: DatasetKey; kind: 'gForce' };
}

/** A chart panel definition: which series key it renders and its metadata. */
interface PanelDef {
  option: SeriesOption;
  canvasId: string;
}

/** A vehicle column: heading plus its speed and altitude panels. */
interface VehicleColumn {
  vehicle: Vehicle;
  heading: string;
  panels: PanelDef[];
}

const SPEED_COLOR = '#FF8014';
const ALTITUDE_COLOR = '#4A9EFF';
const G_FORCE_COLOR = '#3ECF8E';
/**
 * Color of the current (live) flight line on every chart. The current flight
 * is always drawn orange regardless of which metric the chart shows, so it is
 * instantly recognizable; comparison flights keep their per-flight colors.
 */
const CURRENT_FLIGHT_COLOR = '#FF8014';

const VEHICLE_COLUMNS: VehicleColumn[] = [
  {
    vehicle: 'superHeavy',
    heading: 'Super Heavy',
    panels: [
      {
        canvasId: 'chart-speed-super-heavy',
        option: { key: 'speedSuperHeavy', label: 'Speed', yLabel: 'Speed', lineColor: SPEED_COLOR },
      },
      {
        canvasId: 'chart-altitude-super-heavy',
        option: { key: 'altitudeSuperHeavy', label: 'Altitude', yLabel: 'Altitude', lineColor: ALTITUDE_COLOR },
      },
      {
        canvasId: 'chart-g-force-super-heavy',
        option: {
          key: 'gForceSuperHeavy',
          label: 'G-Force',
          yLabel: 'G-force (g)',
          lineColor: G_FORCE_COLOR,
          derive: { from: 'speedSuperHeavy', kind: 'gForce' },
        },
      },
    ],
  },
  {
    vehicle: 'starship',
    heading: 'Starship',
    panels: [
      {
        canvasId: 'chart-speed-starship',
        option: { key: 'speedStarship', label: 'Speed', yLabel: 'Speed', lineColor: SPEED_COLOR },
      },
      {
        canvasId: 'chart-altitude-starship',
        option: { key: 'altitudeStarship', label: 'Altitude', yLabel: 'Altitude', lineColor: ALTITUDE_COLOR },
      },
      {
        canvasId: 'chart-g-force-starship',
        option: {
          key: 'gForceStarship',
          label: 'G-Force',
          yLabel: 'G-force (g)',
          lineColor: G_FORCE_COLOR,
          derive: { from: 'speedStarship', kind: 'gForce' },
        },
      },
    ],
  },
];

/** Flattened lookup of every panel plus its owning column, keyed by series key. */
interface FlatPanel {
  column: VehicleColumn;
  panel: PanelDef;
}

const PANEL_BY_KEY: Map<SeriesKey, FlatPanel> = (() => {
  const map = new Map<SeriesKey, FlatPanel>();
  for (const column of VEHICLE_COLUMNS) {
    for (const panel of column.panels) {
      map.set(panel.option.key, { column, panel });
    }
  }
  return map;
})();

/** Query-string parameter that selects a single chart for OBS overlay mode. */
const POPOUT_PARAM = 'popout';
/** Query-string parameter carrying comma-separated comparison flight filenames. */
const COMPARE_PARAM = 'compare';

/**
 * Return the FlatPanel for a popout key string, or null if the key is unknown.
 * Used by the app bootstrap to decide whether to render overlay mode.
 */
export function resolvePopoutKey(raw: string | null): SeriesKey | null {
  if (raw && PANEL_BY_KEY.has(raw as SeriesKey)) {
    return raw as SeriesKey;
  }
  return null;
}

/**
 * Parse the `compare` query parameter into a list of flight filenames.
 * Returns an empty array when the parameter is absent or empty.
 */
export function parseCompareParam(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/**
 * Build the standalone overlay URL for a given chart, suitable for an OBS
 * Browser Source. Uses the current origin + path so it works in dev and prod.
 * The currently selected comparison flights are encoded so the overlay draws
 * the same reference lines as the dashboard.
 */
export function buildPopoutUrl(key: SeriesKey, compareFilenames: string[] = []): string {
  const url = new URL(window.location.href);
  // Drop any existing query/hash, then set just the params we care about.
  url.search = '';
  url.hash = '';
  url.searchParams.set(POPOUT_PARAM, key);
  if (compareFilenames.length > 0) {
    url.searchParams.set(COMPARE_PARAM, compareFilenames.join(','));
  }
  return url.toString();
}

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

/** Standard gravitational acceleration in m/s². */
const STANDARD_GRAVITY = 9.80665;
/** Conversion factor from km/h to m/s. */
const KMH_TO_MS = 1000 / 3600;
/**
 * Maximum time gap (seconds) between two speed samples for which a derivative
 * is still meaningful. Larger gaps produce a break in the G-force line.
 */
const G_FORCE_MAX_DT_SECONDS = 10;

/**
 * Derive a G-force series from a speed series.
 *
 * Speed is assumed to be in km/h (matching the telemetry stream). G-force is
 * the acceleration (time-derivative of speed) expressed in units of standard
 * gravity: g = (dv/dt) / 9.80665.
 *
 * Points are computed at the midpoint between consecutive speed samples using
 * a backward finite difference. Where two samples are separated by more than
 * G_FORCE_MAX_DT_SECONDS, a null gap marker is emitted instead so Chart.js
 * breaks the line rather than drawing across the gap (matching the other
 * series, which use deduplicateByX for the same effect).
 */
function computeGForce(points: TimeSeriesPoint[]): { x: number; y: number | null }[] {
  if (points.length < 2) return [];

  // Sort/deduplicate by MET seconds so the derivative is monotonic in time.
  const map = new Map<number, number>();
  for (const p of points) {
    const t = parseMETToSeconds(p.missionElapsedTime);
    if (!map.has(t)) {
      map.set(t, p.value);
    }
  }
  const sorted = Array.from(map.entries())
    .map(([x, v]) => ({ x, v }))
    .sort((a, b) => a.x - b.x);

  const result: { x: number; y: number | null }[] = [];
  for (let i = 1; i < sorted.length; i++) {
    const dt = sorted[i].x - sorted[i - 1].x;
    if (dt <= 0) continue;

    if (dt > G_FORCE_MAX_DT_SECONDS) {
      // Break the line across the gap instead of interpolating over it.
      result.push({ x: (sorted[i].x + sorted[i - 1].x) / 2, y: null });
      continue;
    }

    const dvMs = (sorted[i].v - sorted[i - 1].v) * KMH_TO_MS;
    const g = dvMs / dt / STANDARD_GRAVITY;
    // Plot at the midpoint of the interval the derivative represents.
    result.push({ x: (sorted[i].x + sorted[i - 1].x) / 2, y: g });
  }
  return result;
}

interface LoadedComparison {
  filename: string;
  name: string;
  data: TimeSeriesStore;
}

/**
 * Runtime state for a single chart panel. Tracks the DOM host used in the main
 * page and, when the panel is popped out, the external window and its host so
 * the chart can be moved back and forth.
 */
interface PanelRuntime {
  panel: PanelDef;
  /** The in-page canvas container that normally holds the chart. */
  inlineContainer: HTMLElement;
  /** The pop-out button for this panel. */
  popButton: HTMLButtonElement;
  /** The external window when popped out, otherwise null. */
  popupWindow: Window | null;
  /** Interval id used to detect the external window closing. */
  popupWatcher: number | null;
}

/**
 * Time-Series Graphs component.
 * Renders a two-column layout of interactive line charts: the left column
 * shows Super Heavy (speed + altitude) and the right column shows Starship
 * (speed + altitude). Supports Chart.js zoom/pan on every chart.
 * Overlays one or more previous flights for comparison using a shared
 * numeric time axis (seconds from T-0).
 */
export class TimeSeriesGraphs {
  private container: HTMLElement;
  private stateManager: StateManager;
  private compareContainer!: HTMLElement;
  private latestTimeSeries: TimeSeriesStore | null = null;

  /** One Chart instance per panel, keyed by the series key. */
  private charts: Map<SeriesKey, Chart<'line'>> = new Map();

  /** Per-panel runtime state, keyed by series key. */
  private panelRuntimes: Map<SeriesKey, PanelRuntime> = new Map();

  // Previous flight comparison state
  private previousFlights: PreviousFlightInfo[] = [];
  private selectedCompareFilenames: Set<string> = new Set();
  private loadedComparisons: LoadedComparison[] = [];

  /**
   * When set, the component renders a single chart in transparent "overlay"
   * mode (for OBS Browser Sources) instead of the full two-column dashboard.
   */
  private readonly overlayKey: SeriesKey | null;

  constructor(
    container: HTMLElement,
    stateManager: StateManager,
    options: { overlayKey?: SeriesKey; compareFilenames?: string[] } = {},
  ) {
    this.container = container;
    this.stateManager = stateManager;
    this.overlayKey = options.overlayKey ?? null;

    if (this.overlayKey) {
      this.renderOverlay(this.overlayKey);
    } else {
      this.render();
    }
    this.initCharts();
    if (this.overlayKey) {
      // Overlay mode: load the comparison flights encoded in the URL so the
      // same reference lines appear as on the dashboard.
      this.loadOverlayComparisons(options.compareFilenames ?? []);
    } else {
      this.loadPreviousFlightList();
    }
    this.stateManager.subscribe((state) => {
      this.latestTimeSeries = state.timeSeries;
      this.updateCharts(state.timeSeries);
    });
  }

  /**
   * Load a fixed set of comparison flights by filename (overlay mode). Unlike
   * the dashboard flow, there is no checkbox UI — the selection is fixed by the
   * URL. Colors are assigned by fetching the flight list so they match the
   * dashboard's swatch ordering.
   */
  private async loadOverlayComparisons(filenames: string[]): Promise<void> {
    if (filenames.length === 0) return;

    // Fetch the flight list so overlay colors line up with dashboard swatches.
    this.previousFlights = await fetchPreviousFlightList();

    const loadPromises = filenames.map(async (filename) => {
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
    this.updateCharts(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
  }

  /**
   * Render a single chart filling the viewport with a transparent background.
   * No header, toolbar, or compare controls — just the chart, for use as an
   * OBS overlay. Comparison overlays are intentionally omitted here.
   */
  private renderOverlay(key: SeriesKey): void {
    const flat = PANEL_BY_KEY.get(key);
    if (!flat) return;

    // Make the host document transparent and full-bleed.
    document.documentElement.classList.add('tsg-overlay-root');
    document.body.classList.add('tsg-overlay-root');

    this.container.innerHTML = '';

    const wrapper = document.createElement('div');
    wrapper.className = 'time-series-graphs time-series-graphs--overlay';

    const canvasContainer = document.createElement('div');
    canvasContainer.className =
      'time-series-graphs__canvas-container time-series-graphs__canvas-container--overlay';

    const canvas = document.createElement('canvas');
    canvas.id = flat.panel.canvasId;
    canvasContainer.appendChild(canvas);
    wrapper.appendChild(canvasContainer);
    this.container.appendChild(wrapper);

    this.panelRuntimes.set(key, {
      panel: flat.panel,
      inlineContainer: canvasContainer,
      // No pop-out control in overlay mode; use a detached button placeholder.
      popButton: document.createElement('button'),
      popupWindow: null,
      popupWatcher: null,
    });
  }

  private render(): void {
    this.container.innerHTML = '';

    const wrapper = document.createElement('div');
    wrapper.className = 'time-series-graphs';

    // Row 1: Title + primary controls (reset)
    const header = document.createElement('div');
    header.className = 'time-series-graphs__header';

    const title = document.createElement('h2');
    title.className = 'time-series-graphs__heading';
    title.textContent = 'Time-Series';

    const controls = document.createElement('div');
    controls.className = 'time-series-graphs__controls';

    const resetBtn = document.createElement('button');
    resetBtn.className = 'time-series-graphs__reset-btn';
    resetBtn.textContent = 'Reset Zoom';
    resetBtn.setAttribute('aria-label', 'Reset zoom');
    resetBtn.addEventListener('click', () => {
      for (const chart of this.charts.values()) {
        chart.resetZoom();
      }
    });

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

    // Row 3: Two vehicle columns, each with stacked speed + altitude charts
    const columns = document.createElement('div');
    columns.className = 'time-series-graphs__columns';

    for (const column of VEHICLE_COLUMNS) {
      const columnEl = document.createElement('div');
      columnEl.className = 'time-series-graphs__column';

      const columnHeading = document.createElement('h3');
      columnHeading.className = 'time-series-graphs__column-heading';
      columnHeading.textContent = column.heading;
      columnEl.appendChild(columnHeading);

      for (const panel of column.panels) {
        const panelEl = document.createElement('div');
        panelEl.className = 'time-series-graphs__panel';

        const panelHeader = document.createElement('div');
        panelHeader.className = 'time-series-graphs__panel-header';

        const panelHeading = document.createElement('span');
        panelHeading.className = 'time-series-graphs__panel-heading';
        panelHeading.textContent = panel.option.label;
        panelHeader.appendChild(panelHeading);

        const actions = document.createElement('div');
        actions.className = 'time-series-graphs__panel-actions';

        const copyLinkButton = document.createElement('button');
        copyLinkButton.className = 'time-series-graphs__link-btn';
        copyLinkButton.type = 'button';
        copyLinkButton.textContent = 'Copy Link';
        copyLinkButton.setAttribute(
          'aria-label',
          `Copy the OBS overlay link for ${column.heading} ${panel.option.label}`,
        );
        copyLinkButton.addEventListener('click', () =>
          this.copyOverlayLink(panel.option.key, copyLinkButton),
        );
        actions.appendChild(copyLinkButton);

        const popButton = document.createElement('button');
        popButton.className = 'time-series-graphs__pop-btn';
        popButton.type = 'button';
        popButton.textContent = 'Pop Out';
        popButton.setAttribute(
          'aria-label',
          `Open ${column.heading} ${panel.option.label} in a separate window`,
        );
        popButton.addEventListener('click', () => this.togglePopOut(panel.option.key));
        actions.appendChild(popButton);

        panelHeader.appendChild(actions);
        panelEl.appendChild(panelHeader);

        const canvasContainer = document.createElement('div');
        canvasContainer.className = 'time-series-graphs__canvas-container';

        const canvas = document.createElement('canvas');
        canvas.id = panel.canvasId;
        canvasContainer.appendChild(canvas);
        panelEl.appendChild(canvasContainer);

        columnEl.appendChild(panelEl);

        this.panelRuntimes.set(panel.option.key, {
          panel,
          inlineContainer: canvasContainer,
          popButton,
          popupWindow: null,
          popupWatcher: null,
        });
      }

      columns.appendChild(columnEl);
    }

    wrapper.appendChild(header);
    wrapper.appendChild(toolbar);
    wrapper.appendChild(columns);
    this.container.appendChild(wrapper);
  }

  private createChartConfig(option: SeriesOption): ChartConfiguration<'line'> {
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

  private initCharts(): void {
    for (const runtime of this.panelRuntimes.values()) {
      this.createChartIn(runtime.inlineContainer, runtime.panel);
    }
  }

  /**
   * Create a Chart in the given container's canvas and register it under the
   * panel's series key. Replaces any existing chart for that key. Returns the
   * new chart (or null if no canvas was found).
   */
  private createChartIn(container: HTMLElement, panel: PanelDef): Chart<'line'> | null {
    const canvas = container.querySelector('canvas') as HTMLCanvasElement | null;
    if (!canvas) return null;

    const existing = this.charts.get(panel.option.key);
    if (existing) {
      // The existing chart may be bound to a canvas in a window that has
      // already closed; destroying it can throw, so guard it.
      try {
        existing.destroy();
      } catch {
        /* ignore: canvas/context already gone */
      }
      this.charts.delete(panel.option.key);
    }

    const chart = new Chart(canvas, this.createChartConfig(panel.option)) as Chart<'line'>;
    this.charts.set(panel.option.key, chart);
    // Feed it the latest data immediately so it isn't blank until the next tick.
    this.updatePanel(panel, this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
    return chart;
  }

  /**
   * Copy this chart's standalone overlay URL to the clipboard and give brief
   * visual feedback on the button. The URL renders the single chart with a
   * transparent background — paste it into an OBS Browser Source.
   */
  private async copyOverlayLink(key: SeriesKey, button: HTMLButtonElement): Promise<void> {
    const url = buildPopoutUrl(key, Array.from(this.selectedCompareFilenames));
    const original = button.textContent;
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url);
        ok = true;
      }
    } catch {
      ok = false;
    }

    if (!ok) {
      // Fallback: select the URL in a temporary field so the user can copy it.
      const temp = document.createElement('input');
      temp.value = url;
      document.body.appendChild(temp);
      temp.select();
      try {
        ok = document.execCommand('copy');
      } catch {
        ok = false;
      }
      document.body.removeChild(temp);
    }

    button.textContent = ok ? 'Copied!' : 'Copy failed';
    window.setTimeout(() => {
      button.textContent = original;
    }, 1500);
  }

  /** Toggle a panel between inline and popped-out states. */
  private togglePopOut(key: SeriesKey): void {
    const runtime = this.panelRuntimes.get(key);
    if (!runtime) return;
    if (runtime.popupWindow && !runtime.popupWindow.closed) {
      runtime.popupWindow.close();
      // popupWatcher will handle re-docking.
    } else {
      this.openPopOut(runtime);
    }
  }

  /**
   * Open a panel's chart in a separate, transparent-background window.
   * Moves the live chart into the popup so it keeps updating.
   */
  private openPopOut(runtime: PanelRuntime): void {
    const { panel } = runtime;
    const width = 640;
    const height = 420;
    const popup = window.open(
      '',
      `tsg-popout-${panel.option.key}`,
      `width=${width},height=${height}`,
    );
    if (!popup) {
      // Popup blocked; leave the chart inline.
      return;
    }

    const title = `${panel.option.label} — ${panel.option.yLabel}`;
    // Transparent background at every level so nothing paints behind the chart.
    popup.document.open();
    popup.document.write(`<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>${title}</title>
    <style>
      html, body {
        margin: 0;
        padding: 0;
        width: 100%;
        height: 100%;
        background: transparent !important;
      }
      #popout-host {
        position: absolute;
        inset: 0;
        background: transparent;
      }
      #popout-host canvas {
        width: 100% !important;
        height: 100% !important;
      }
    </style>
  </head>
  <body>
    <div id="popout-host"></div>
  </body>
</html>`);
    popup.document.close();

    const host = popup.document.getElementById('popout-host');
    if (!host) {
      popup.close();
      return;
    }

    // Create a fresh canvas inside the popup and move the live chart there.
    const canvas = popup.document.createElement('canvas');
    canvas.id = panel.canvasId;
    host.appendChild(canvas);

    runtime.popupWindow = popup;
    runtime.popButton.textContent = 'Pop In';
    runtime.popButton.classList.add('time-series-graphs__pop-btn--active');

    this.createChartIn(host, panel);

    // Watch for the popup being closed (via its close button or navigation).
    const watcher = window.setInterval(() => {
      if (popup.closed) {
        this.handlePopupClosed(runtime);
      }
    }, 400);
    runtime.popupWatcher = watcher;

    // Also react to explicit unload for a snappier re-dock.
    popup.addEventListener('beforeunload', () => this.handlePopupClosed(runtime));

    // Close the popup automatically if the main page unloads.
    window.addEventListener('beforeunload', () => {
      if (runtime.popupWindow && !runtime.popupWindow.closed) {
        runtime.popupWindow.close();
      }
    });
  }

  /** Re-dock a panel's chart into the main page after its popup closes. */
  private handlePopupClosed(runtime: PanelRuntime): void {
    if (runtime.popupWatcher !== null) {
      window.clearInterval(runtime.popupWatcher);
      runtime.popupWatcher = null;
    }
    if (!runtime.popupWindow) {
      return; // Already re-docked.
    }
    runtime.popupWindow = null;
    runtime.popButton.textContent = 'Pop Out';
    runtime.popButton.classList.remove('time-series-graphs__pop-btn--active');

    // Ensure the inline container still has a canvas to draw into.
    if (!runtime.inlineContainer.querySelector('canvas')) {
      const canvas = document.createElement('canvas');
      canvas.id = runtime.panel.canvasId;
      runtime.inlineContainer.appendChild(canvas);
    }
    this.createChartIn(runtime.inlineContainer, runtime.panel);
  }

  /**
   * Convert TimeSeriesPoints to {x, y} scatter data using MET string → seconds.
   * Sorted by x and deduplicated to prevent line looping.
   */
  private pointsToXY(points: TimeSeriesPoint[]): { x: number; y: number | null }[] {
    return deduplicateByX(
      points.map((p) => ({
        x: parseMETToSeconds(p.missionElapsedTime),
        y: p.value,
      })),
    );
  }

  /**
   * Build the {x, y} data for a panel from a store, resolving derived series
   * (such as G-force) from their base series.
   */
  private optionXY(option: SeriesOption, store: TimeSeriesStore): { x: number; y: number | null }[] {
    if (option.derive) {
      const source = store[option.derive.from] ?? [];
      // computeGForce inserts null gap markers itself; spanGaps:false breaks the line.
      return computeGForce(source);
    }
    const points = store[option.key as DatasetKey] ?? [];
    return this.pointsToXY(points);
  }

  private static readonly EMPTY_STORE: TimeSeriesStore = {
    speedSuperHeavy: [],
    speedStarship: [],
    altitudeSuperHeavy: [],
    altitudeStarship: [],
  };

  private updateCharts(timeSeries: TimeSeriesStore): void {
    for (const column of VEHICLE_COLUMNS) {
      for (const panel of column.panels) {
        this.updatePanel(panel, timeSeries);
      }
    }
  }

  private updatePanel(panel: PanelDef, timeSeries: TimeSeriesStore): void {
    const chart = this.charts.get(panel.option.key);
    if (!chart) return;

    const option = panel.option;
    const liveXY = this.optionXY(option, timeSeries);

    const datasets: any[] = [];

    // Derived series (G-force) render as a plain line without an area fill,
    // since the value swings above and below zero. In overlay mode we also
    // drop the fill so nothing paints a translucent wedge over the video.
    const fillLive = !option.derive && !this.overlayKey;

    // Only include the live dataset if there's actual data
    if (liveXY.length > 0) {
      datasets.push({
        label: option.label,
        data: liveXY,
        borderColor: CURRENT_FLIGHT_COLOR,
        backgroundColor: `${CURRENT_FLIGHT_COLOR}22`,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.2,
        fill: fillLive,
        spanGaps: false,
      });
    }

    // Add each comparison flight as an overlay dataset
    for (let i = 0; i < this.loadedComparisons.length; i++) {
      const comp = this.loadedComparisons[i];
      const compXY = this.optionXY(option, comp.data);
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

    chart.data = { datasets };
    chart.update('none');
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

    // Refresh charts (use empty store if no live data yet)
    this.updateCharts(this.latestTimeSeries ?? TimeSeriesGraphs.EMPTY_STORE);
  }
}
