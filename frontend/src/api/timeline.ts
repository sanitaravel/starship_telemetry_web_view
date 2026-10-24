import type { TimelineStatusPayload, TimelineStatusValue } from '../core/types';
import { isTimelineStatusShape } from './websocket';

/**
 * The backend's mission timeline status endpoints (backend/src/api/routes/timeline.py).
 * Overlays only ever call fetchTimelineStatus: they never change anything.
 */

/** The current statuses, or null when the backend answered with something unusable. */
export async function fetchTimelineStatus(): Promise<TimelineStatusPayload | null> {
  const response = await fetch('/api/timeline/status');
  if (!response.ok) throw new Error(`Loading timeline statuses failed (${response.status})`);
  const body = (await response.json()) as unknown;
  return typeof body === 'object' && body !== null && isTimelineStatusShape(body as Record<string, unknown>)
    ? (body as TimelineStatusPayload)
    : null;
}

/**
 * Sets an event's status by hand, or with null hands it back to automatic
 * confirmation. Rejects for an unknown event (404) or status (422).
 */
export async function overrideTimelineStatus(
  eventId: string,
  status: TimelineStatusValue | null
): Promise<TimelineStatusPayload> {
  const response = await fetch(`/api/timeline/${encodeURIComponent(eventId)}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  if (!response.ok) throw new Error(`Setting the status of ${eventId} failed (${response.status})`);
  return response.json();
}

/** Tells the backend which mission the run is, so whose timeline it follows (null: none). */
export async function setActiveTimelineMission(missionId: string | null): Promise<TimelineStatusPayload> {
  const response = await fetch('/api/timeline/active', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mission_id: missionId }),
  });
  if (!response.ok) throw new Error(`Setting the active mission failed (${response.status})`);
  return response.json();
}
