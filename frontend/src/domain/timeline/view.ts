/**
 * What the timeline panel, its overlay and the graph markers show about an
 * event's status, and which events are "current" and "next" at a given MET.
 * Pure logic, no DOM.
 */

import type { TimelineEventStatus, TimelineStatusValue } from '../../core/types';
import { formatTimelineTime } from './parse';
import type { TimelineEvent, TimelineVehicle } from './types';

export const STATUS_LABELS: Record<TimelineStatusValue, string> = {
  not_yet: 'Not yet',
  pending: 'Needs confirmation',
  success: 'Success',
  partial: 'Partial success',
  failure: 'Failure',
  skipped: 'Skipped',
};

/**
 * One colour per status. The circles also differ in shape (ring, pulsing
 * ring, ✓, half, ✕, dashed slash), so the colour never carries the status alone.
 */
export const STATUS_COLORS: Record<TimelineStatusValue, string> = {
  not_yet: '#8a8a8a',
  pending: '#ffb020',
  success: '#34c759',
  partial: '#b5d334',
  failure: '#ff3b30',
  skipped: '#8a8a8a',
};

export const VEHICLE_TAGS: Record<TimelineVehicle, string> = {
  superheavy: 'SH',
  starship: 'SS',
  both: 'ALL',
};

/** The status an event has when the backend does not (yet) follow it. */
export const NO_STATUS: Pick<TimelineEventStatus, 'status' | 'source' | 'evidence' | 'met'> = {
  status: 'not_yet',
  source: 'auto',
  evidence: null,
  met: null,
};

/**
 * Index of the current event: the last one planned at or before `met`
 * (of two events at T-0, the second). Null before the first event or
 * without a MET.
 */
export function currentEventIndex(events: readonly TimelineEvent[], met: number | null): number | null {
  if (met === null) return null;
  let found: number | null = null;
  for (let i = 0; i < events.length; i++) {
    if (events[i].t <= met) found = i;
    else break;
  }
  return found;
}

/**
 * Index of the next event: the first one planned after `met`. Without a MET,
 * null; before the first event, the first one.
 */
export function nextEventIndex(events: readonly TimelineEvent[], met: number | null): number | null {
  if (met === null) return null;
  const index = events.findIndex((e) => e.t > met);
  return index === -1 ? null : index;
}

/** "m:ss", or "h:mm:ss" from one hour; the whole seconds still to go. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.ceil(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

/** The MET as the panel's header shows it, "T-00:12:31" / "T+00:01:33", or "—" when unknown. */
export function formatClock(met: number | null): string {
  return met === null ? '—' : formatTimelineTime(Math.round(met));
}

/** "2 need confirmation"; empty for none. */
export function pendingText(count: number): string {
  if (count <= 0) return '';
  return count === 1 ? '1 needs confirmation' : `${count} need confirmation`;
}

/** The tooltip of a status circle: label, who decided, why and when. */
export function statusTooltip(event: TimelineEvent, status: Pick<TimelineEventStatus, 'status' | 'source' | 'evidence' | 'met'>): string {
  const lines = [
    `${event.event}`,
    `${STATUS_LABELS[status.status]} (${status.source === 'human' ? 'set by hand' : 'automatic'})`,
  ];
  if (status.evidence) lines.push(status.evidence);
  if (status.met !== null) lines.push(`Decided at ${formatTimelineTime(Math.round(status.met))}`);
  return lines.join('\n');
}

/** The vehicle a graph shows. */
export type GraphVehicle = 'superheavy' | 'starship';

/**
 * The events drawn on a vehicle's graphs, from T+0 on. Super Heavy graphs
 * show the Super Heavy and shared events. Starship graphs show the Super
 * Heavy and shared events before stage separation and the Starship and
 * shared ones from then on; with separation unknown (null), every event.
 */
export function timelineEventsForVehicle(
  events: readonly TimelineEvent[],
  vehicle: GraphVehicle,
  separationMetSeconds: number | null,
): TimelineEvent[] {
  return events.filter((e) => {
    if (e.t < 0) return false;
    if (e.vehicle === 'both') return true;
    if (vehicle === 'superheavy') return e.vehicle === 'superheavy';
    if (separationMetSeconds === null) return true;
    return e.vehicle === 'superheavy' ? e.t < separationMetSeconds : e.t >= separationMetSeconds;
  });
}
