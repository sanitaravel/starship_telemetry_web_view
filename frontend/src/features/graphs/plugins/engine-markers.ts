/**
 * Chart markers for engine events: which events belong on which series,
 * and the tooltip text that explains them. Pure logic, no DOM or Chart.js.
 */

import type { EngineEvent, EngineEventKind } from '../../../domain/engines/events';
import { engineLabel, ringNameOf, VEHICLE_NAMES, type Vehicle } from '../../../domain/engines/layout';
import type { SeriesKey } from '../types';
import { formatSecondsToMET } from '../../../core/met';

/** A marker drawn by the engineEvents plugin. */
export interface EngineMarker {
  /** MET of the event in seconds (x value). */
  x: number;
  /** Set for events inside a panel gap: the gap's [start, end] MET. */
  range?: [number, number];
  color: string;
  kind: EngineEventKind;
  tooltipLines: string[];
}

/** Marker colours of the live flight; comparison flights use their own colour. */
export const ENGINE_OUT_COLOR = '#ff3b30';
export const FAILED_TO_LIGHT_COLOR = '#ffb020';

/** The vehicle a series (or its base series, for derived ones) belongs to. */
export function seriesVehicle(key: SeriesKey): Vehicle {
  return key.endsWith('SuperHeavy') ? 'superheavy' : 'starship';
}

/**
 * Engine events shown on a vehicle's series. Super Heavy charts show only
 * Super Heavy events. Starship charts show the stack's (Super Heavy) events
 * before stage separation and Starship events; with separation unknown
 * (null), every Super Heavy event.
 */
export function eventsForSeries(
  events: readonly EngineEvent[],
  vehicle: Vehicle,
  separationMetSeconds: number | null,
): EngineEvent[] {
  if (vehicle === 'superheavy') return events.filter((e) => e.vehicle === 'superheavy');
  return events.filter(
    (e) =>
      e.vehicle === 'starship' ||
      separationMetSeconds === null ||
      e.metSeconds < separationMetSeconds,
  );
}

const KIND_TEXT: Record<EngineEventKind, string> = {
  engine_out: 'Engine out',
  died_after_relight: 'Engine out after relight',
  failed_to_light: 'Failed to light',
};

/** MET as shown in the tooltip, e.g. "T+1:52" (same clock as the axis ticks). */
function metText(seconds: number): string {
  return `T${formatSecondsToMET(seconds)}`;
}

/**
 * Engine labels of one ring with runs of 3+ consecutive numbers collapsed:
 * ["E9", "E10", "E11", "E13", "E14"] -> "E9–E11, E13, E14".
 */
function labelsText(labels: readonly string[]): string {
  const parts: string[] = [];
  let i = 0;
  while (i < labels.length) {
    let j = i;
    while (j + 1 < labels.length && labelNumber(labels[j + 1]) === labelNumber(labels[j]) + 1) j++;
    if (j - i >= 2) parts.push(`${labels[i]}–${labels[j]}`);
    else parts.push(...labels.slice(i, j + 1));
    i = j + 1;
  }
  return parts.join(', ');
}

/** Engine number of a label ("E17" -> 17), NaN if it has none. */
function labelNumber(label: string): number {
  return Number(label.replace(/^E/, ''));
}

/** Engine labels grouped by ring: "E5 (middle ring), E14–E17, E22 (outer ring)". */
function enginesText(engineIds: readonly string[]): string {
  const groups: { ring: string | undefined; labels: string[] }[] = [];
  for (const id of engineIds) {
    const ring = ringNameOf(id);
    const last = groups[groups.length - 1];
    if (last && last.ring === ring) last.labels.push(engineLabel(id));
    else groups.push({ ring, labels: [engineLabel(id)] });
  }
  return groups
    .map((g) => (g.ring ? `${labelsText(g.labels)} (${g.ring})` : labelsText(g.labels)))
    .join(', ');
}

/**
 * Tooltip line for an event, e.g.
 * "Engine out: Super Heavy E17 (outer ring) · T+1:52 · 32/33 running".
 * flightName prefixes comparison flights: "IFT-12 — Engine out: …".
 */
export function formatEngineEvent(event: EngineEvent, flightName?: string): string {
  const time = event.metRange
    ? `between ${metText(event.metRange[0])} and ${metText(event.metRange[1])}`
    : metText(event.metSeconds);
  const text =
    `${KIND_TEXT[event.kind]}: ${VEHICLE_NAMES[event.vehicle]} ${enginesText(event.engineIds)}` +
    ` · ${time} · ${event.runningAfter}/${event.expected} running`;
  return flightName ? `${flightName} — ${text}` : text;
}

/** One marker per event; color undefined uses the live flight's colours. */
export function eventMarkers(
  events: readonly EngineEvent[],
  color?: string,
  flightName?: string,
): EngineMarker[] {
  return events.map((event) => {
    const marker: EngineMarker = {
      x: event.metSeconds,
      color: color ?? (event.kind === 'failed_to_light' ? FAILED_TO_LIGHT_COLOR : ENGINE_OUT_COLOR),
      kind: event.kind,
      tooltipLines: [formatEngineEvent(event, flightName)],
    };
    if (event.metRange) marker.range = [event.metRange[0], event.metRange[1]];
    return marker;
  });
}
