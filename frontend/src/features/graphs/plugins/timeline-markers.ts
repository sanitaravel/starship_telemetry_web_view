/**
 * Chart markers for the mission timeline: which events belong on which
 * series, and the tooltip text that explains them. They are drawn by the
 * engineEvents plugin together with the engine markers. Pure logic.
 */

import type { TimelineEventStatus } from '../../../core/types';
import type { TimelineEvent } from '../../../domain/timeline/types';
import { formatTimelineTime } from '../../../domain/timeline/parse';
import { NO_STATUS, STATUS_COLORS, STATUS_LABELS, timelineEventsForVehicle } from '../../../domain/timeline/view';
import type { Vehicle } from '../../../domain/engines/layout';
import type { EngineMarker } from './engine-markers';

/** Markers of the mission timeline's events (t ≥ 0) on a vehicle's series, in the colour of each status. */
export function timelineMarkers(
  events: readonly TimelineEvent[],
  statusOf: (eventId: string) => Pick<TimelineEventStatus, 'status' | 'source' | 'evidence'> | undefined,
  vehicle: Vehicle,
  separationMetSeconds: number | null,
): EngineMarker[] {
  return timelineEventsForVehicle(events, vehicle, separationMetSeconds).map((event) => {
    const status = statusOf(event.id) ?? NO_STATUS;
    const lines = [
      `${formatTimelineTime(event.t)} · ${event.event}`,
      `${STATUS_LABELS[status.status]} (${status.source === 'human' ? 'set by hand' : 'automatic'})`,
    ];
    if (status.evidence) lines.push(status.evidence);
    return { x: event.t, color: STATUS_COLORS[status.status], kind: 'timeline', tooltipLines: lines };
  });
}
