import { describe, it, expect } from 'vitest';
import type { TimelineEventStatus } from '../../../core/types';
import type { TimelineEvent } from '../../../domain/timeline/types';
import { STATUS_COLORS } from '../../../domain/timeline/view';
import { timelineMarkers } from './timeline-markers';

function ev(id: string, t: number, vehicle: TimelineEvent['vehicle'], event = id): TimelineEvent {
  return { id, t, event, vehicle, auto: { kind: 'manual' } };
}

const EVENTS = [
  ev('hold', -30, 'both'),
  ev('liftoff', 0, 'both', 'Liftoff'),
  ev('meco', 158, 'superheavy', 'Super Heavy MECO'),
  ev('seco', 500, 'starship', 'Starship engine cutoff'),
];

const statuses: Record<string, Pick<TimelineEventStatus, 'status' | 'source' | 'evidence'>> = {
  liftoff: { status: 'success', source: 'auto', evidence: 'Liftoff at T+00:00:00' },
  meco: { status: 'partial', source: 'human', evidence: null },
};

const statusOf = (id: string) => statuses[id];

describe('timelineMarkers', () => {
  it('marks events from T+0 on, never the countdown', () => {
    const markers = timelineMarkers(EVENTS, statusOf, 'superheavy', null);
    expect(markers.map((m) => m.x)).toEqual([0, 158]);
    expect(markers.every((m) => m.kind === 'timeline')).toBe(true);
  });

  it('colours each marker by its status, grey while the backend does not follow the event', () => {
    const markers = timelineMarkers(EVENTS, statusOf, 'starship', null);
    expect(markers.map((m) => m.color)).toEqual([
      STATUS_COLORS.success,
      STATUS_COLORS.partial,
      STATUS_COLORS.not_yet,
    ]);
  });

  it('puts the time, text, status, who set it and the evidence in the tooltip', () => {
    const [liftoff, meco] = timelineMarkers(EVENTS, statusOf, 'superheavy', null);
    expect(liftoff.tooltipLines).toEqual(['T+00:00:00 · Liftoff', 'Success (automatic)', 'Liftoff at T+00:00:00']);
    expect(meco.tooltipLines).toEqual(['T+00:02:38 · Super Heavy MECO', 'Partial success (set by hand)']);
  });

  it('follows the engine markers vehicle rule: Starship graphs switch vehicles at separation', () => {
    const xs = (vehicle: 'superheavy' | 'starship', separation: number | null) =>
      timelineMarkers(EVENTS, statusOf, vehicle, separation).map((m) => m.x);
    expect(xs('superheavy', 160)).toEqual([0, 158]);
    expect(xs('starship', 160)).toEqual([0, 158, 500]);
    expect(xs('starship', 100)).toEqual([0, 500]);
    expect(xs('starship', 1000)).toEqual([0, 158]);
    expect(xs('starship', null)).toEqual([0, 158, 500]);
  });
});
