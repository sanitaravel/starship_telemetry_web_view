/**
 * The widgets an OBS overlay page can show (`?overlay=<id>`), with the
 * labels the OBS popover lists and the Browser Source size it suggests.
 */

/** The graphs' series keys; mirrors SeriesKey in features/graphs/types.ts (app/overlay.test.ts checks). */
export type GraphSeriesId =
  | 'speedSuperHeavy'
  | 'speedStarship'
  | 'altitudeSuperHeavy'
  | 'altitudeStarship'
  | 'accelerationSuperHeavy'
  | 'accelerationStarship';

export type OverlayWidgetId =
  | 'telemetry'
  | 'met'
  | 'engines-superheavy'
  | 'engines-starship'
  | 'globe'
  | 'timeline'
  | 'timeline-next'
  | `graph-${GraphSeriesId}`;

export interface OverlayWidgetInfo {
  id: OverlayWidgetId;
  label: string;
  /** Suggested OBS Browser Source size in CSS pixels. */
  size: { width: number; height: number };
}

const GRAPH_SIZE = { width: 800, height: 400 };

/** Every graph series with its label, in the graphs' select order. */
export const GRAPH_SERIES: readonly { key: GraphSeriesId; label: string }[] = [
  { key: 'speedSuperHeavy', label: 'Speed — Super Heavy' },
  { key: 'speedStarship', label: 'Speed — Starship' },
  { key: 'altitudeSuperHeavy', label: 'Altitude — Super Heavy' },
  { key: 'altitudeStarship', label: 'Altitude — Starship' },
  { key: 'accelerationSuperHeavy', label: 'Acceleration — Super Heavy' },
  { key: 'accelerationStarship', label: 'Acceleration — Starship' },
];

/** The overlay id of a graph series. */
export function graphWidgetId(key: GraphSeriesId): OverlayWidgetId {
  return `graph-${key}`;
}

export const OVERLAY_WIDGETS: readonly OverlayWidgetInfo[] = [
  { id: 'telemetry', label: 'Telemetry', size: { width: 420, height: 215 } },
  { id: 'met', label: 'Mission elapsed time', size: { width: 250, height: 80 } },
  { id: 'engines-superheavy', label: 'Super Heavy engines', size: { width: 380, height: 290 } },
  { id: 'engines-starship', label: 'Starship engines', size: { width: 260, height: 240 } },
  { id: 'globe', label: 'Ship position', size: { width: 640, height: 400 } },
  { id: 'timeline', label: 'Mission timeline', size: { width: 460, height: 600 } },
  { id: 'timeline-next', label: 'Mission timeline: current and next event', size: { width: 460, height: 110 } },
  ...GRAPH_SERIES.map(({ key, label }) => ({ id: graphWidgetId(key), label: `Graph: ${label}`, size: GRAPH_SIZE })),
];

const WIDGET_IDS: ReadonlySet<string> = new Set(OVERLAY_WIDGETS.map((w) => w.id));

export function isOverlayWidgetId(value: string): value is OverlayWidgetId {
  return WIDGET_IDS.has(value);
}

/** The widget's info; every OverlayWidgetId has one. */
export function overlayWidget(id: OverlayWidgetId): OverlayWidgetInfo {
  return OVERLAY_WIDGETS.find((w) => w.id === id)!;
}

/** The series a `graph-<key>` id shows, or null for any other widget. */
export function graphSeriesOf(id: OverlayWidgetId): GraphSeriesId | null {
  return id.startsWith('graph-') ? (id.slice('graph-'.length) as GraphSeriesId) : null;
}
