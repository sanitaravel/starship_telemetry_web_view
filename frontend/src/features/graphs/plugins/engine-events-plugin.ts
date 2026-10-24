import type { Chart, ChartArea, ChartType, Plugin } from 'chart.js/auto';
import type { EngineMarker } from './engine-markers';

/** Options of the engineEvents plugin, set by graphs.ts on every update. */
export interface EngineEventsPluginOptions {
  markers?: EngineMarker[];
}

declare module 'chart.js' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface PluginOptionsByType<TType extends ChartType> {
    engineEvents?: EngineEventsPluginOptions;
  }
}

/** How close (px, along x) the mouse must be to a marker line to hover it. */
export const HIT_TOLERANCE_PX = 6;

/** Markers this close (px) to the hovered one share its tooltip. */
const SAME_SPOT_PX = 2;

/** Half width and height of the marker shapes at the top of the chart area. */
const SHAPE_HALF_WIDTH = 5;
const SHAPE_HEIGHT = 9;

const TOOLTIP_CLASS = 'time-series-graphs__engine-tooltip';

/**
 * Index of the marker the mouse is over: the nearest one within `tolerance`
 * px along x, with the mouse inside the chart area. Non-finite pixel
 * positions (markers off the visible range) never hit. Null when none.
 */
export function hitTestMarkers(
  pixelXs: readonly number[],
  mouseX: number,
  mouseY: number,
  area: Pick<ChartArea, 'left' | 'right' | 'top' | 'bottom'>,
  tolerance: number = HIT_TOLERANCE_PX,
): number | null {
  if (mouseX < area.left || mouseX > area.right || mouseY < area.top || mouseY > area.bottom) {
    return null;
  }
  let best: number | null = null;
  let bestDistance = Infinity;
  for (let i = 0; i < pixelXs.length; i++) {
    const distance = Math.abs(pixelXs[i] - mouseX);
    if (Number.isFinite(distance) && distance <= tolerance && distance < bestDistance) {
      best = i;
      bestDistance = distance;
    }
  }
  return best;
}

/** Pixel x of every marker line; NaN for markers outside the visible x range. */
function markerPixels(chart: Chart, markers: readonly EngineMarker[]): number[] {
  const scale = chart.scales.x;
  return markers.map((m) =>
    m.x >= scale.min && m.x <= scale.max ? scale.getPixelForValue(m.x) : Number.NaN,
  );
}

function drawMarkers(chart: Chart, markers: readonly EngineMarker[]): void {
  const area = chart.chartArea;
  const scale = chart.scales.x;
  if (!area || !scale) return;
  const ctx = chart.ctx;
  ctx.save();
  ctx.beginPath();
  ctx.rect(area.left, area.top, area.right - area.left, area.bottom - area.top);
  ctx.clip();

  // Gap-spanning events: a faint band over the gap
  for (const m of markers) {
    if (!m.range || m.range[1] < scale.min || m.range[0] > scale.max) continue;
    const x1 = scale.getPixelForValue(m.range[0]);
    const x2 = scale.getPixelForValue(m.range[1]);
    ctx.globalAlpha = 0.12;
    ctx.fillStyle = m.color;
    ctx.fillRect(Math.min(x1, x2), area.top, Math.max(1, Math.abs(x2 - x1)), area.bottom - area.top);
  }

  const pixels = markerPixels(chart, markers);
  for (let i = 0; i < markers.length; i++) {
    const x = pixels[i];
    if (!Number.isFinite(x)) continue;
    const m = markers[i];
    if (m.kind === 'timeline') {
      // Mission timeline: a faint, finely dashed line; the text comes on hover
      ctx.globalAlpha = 0.55;
      ctx.strokeStyle = m.color;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 4]);
      ctx.beginPath();
      ctx.moveTo(x, area.top);
      ctx.lineTo(x, area.bottom);
      ctx.stroke();
      continue;
    }
    ctx.globalAlpha = 0.8;
    ctx.strokeStyle = m.color;
    ctx.fillStyle = m.color;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, area.top);
    ctx.lineTo(x, area.bottom);
    ctx.stroke();

    ctx.globalAlpha = 1;
    ctx.setLineDash([]);
    ctx.beginPath();
    if (m.kind === 'failed_to_light') {
      // Hollow diamond, so failures to light stand apart from shutdowns
      const midY = area.top + SHAPE_HEIGHT / 2 + 1;
      ctx.moveTo(x, area.top + 1);
      ctx.lineTo(x + SHAPE_HALF_WIDTH, midY);
      ctx.lineTo(x, area.top + SHAPE_HEIGHT + 1);
      ctx.lineTo(x - SHAPE_HALF_WIDTH, midY);
      ctx.closePath();
      ctx.lineWidth = 1.5;
      ctx.stroke();
    } else {
      // Filled triangle pointing down
      ctx.moveTo(x - SHAPE_HALF_WIDTH, area.top);
      ctx.lineTo(x + SHAPE_HALF_WIDTH, area.top);
      ctx.lineTo(x, area.top + SHAPE_HEIGHT);
      ctx.closePath();
      ctx.fill();
    }
  }
  ctx.restore();
}

/** Hover tooltip per chart, created on the first marker hover. */
const tooltips = new WeakMap<Chart, HTMLDivElement>();

function hideTooltip(chart: Chart): void {
  const el = tooltips.get(chart);
  if (el) el.hidden = true;
}

function showTooltip(chart: Chart, lines: readonly string[], markerX: number): void {
  const canvas = chart.canvas;
  const container = canvas.parentElement;
  if (!container) return;
  let el = tooltips.get(chart);
  if (!el || el.parentElement !== container) {
    el = document.createElement('div');
    el.className = TOOLTIP_CLASS;
    el.setAttribute('role', 'tooltip');
    container.appendChild(el);
    tooltips.set(chart, el);
  }
  el.replaceChildren(
    ...lines.map((line) => {
      const row = document.createElement('div');
      row.textContent = line;
      return row;
    }),
  );
  el.hidden = false;

  // Centred under the marker shape, kept inside the container
  const width = el.offsetWidth;
  const maxLeft = Math.max(0, container.clientWidth - width);
  const left = Math.min(Math.max(0, canvas.offsetLeft + markerX - width / 2), maxLeft);
  el.style.left = `${left}px`;
  el.style.top = `${canvas.offsetTop + chart.chartArea.top + SHAPE_HEIGHT + 4}px`;
}

/**
 * Draws engine event markers (options.plugins.engineEvents.markers): a
 * dashed line at each event, a ▼ (shutdowns) or ◇ (failed to light) at the
 * top of the chart area and a band over gap-spanning events. Hovering a
 * marker shows a DOM tooltip naming the engines and hides the regular
 * tooltip. Charts that get no mouse events (the PNG export) only draw.
 */
export const engineEventsPlugin: Plugin<ChartType, EngineEventsPluginOptions> = {
  id: 'engineEvents',

  afterDatasetsDraw(chart, _args, options) {
    const markers = options?.markers;
    if (markers && markers.length > 0) drawMarkers(chart, markers);
  },

  afterEvent(chart, args, options) {
    const event = args.event;
    if (event.type === 'mouseout') {
      hideTooltip(chart);
      return;
    }
    if (event.type !== 'mousemove' || event.x === null || event.y === null) return;
    const markers = options?.markers ?? [];
    const area = chart.chartArea;
    if (markers.length === 0 || !area || !chart.scales.x) {
      hideTooltip(chart);
      return;
    }
    const pixels = markerPixels(chart, markers);
    const hit = hitTestMarkers(pixels, event.x, event.y, area);
    if (hit === null) {
      hideTooltip(chart);
      return;
    }
    const lines = markers
      .filter((_, i) => Math.abs(pixels[i] - pixels[hit]) <= SAME_SPOT_PX)
      .flatMap((m) => m.tooltipLines);
    showTooltip(chart, lines, pixels[hit]);

    // The marker tooltip replaces the regular one (and its crosshair) and
    // the hover dot on the line. Both come back on the next move off the
    // marker, when Chart.js recomputes them from the event.
    const tooltip = chart.tooltip;
    if (tooltip && tooltip.getActiveElements().length > 0) {
      tooltip.setActiveElements([], { x: event.x, y: event.y });
      args.changed = true;
    }
    if (chart.getActiveElements().length > 0) {
      chart.setActiveElements([]);
      args.changed = true;
    }
  },

  afterDestroy(chart) {
    tooltips.get(chart)?.remove();
    tooltips.delete(chart);
  },
};
