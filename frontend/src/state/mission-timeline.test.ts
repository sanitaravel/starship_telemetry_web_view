import { describe, it, expect, vi } from 'vitest';
import { MissionTimeline } from './mission-timeline';
import { TimelineStatusStore } from './timeline-status';
import type { CurrentMission } from './current-mission';
import type { TimelineEventStatus, TimelineStatusPayload } from '../core/types';
import type { TimelineEvent } from '../domain/timeline/types';

function ev(id: string, t = 0): TimelineEvent {
  return { id, t, event: id, vehicle: 'both', auto: { kind: 'manual' } };
}

function status(id: string): TimelineEventStatus {
  return { event_id: id, status: 'not_yet', source: 'auto', evidence: null, met: null, updated_at: '2026-10-24T00:00:00Z' };
}

let revision = 0;
function payload(missionId: string | null, ids: string[]): TimelineStatusPayload {
  return { mission_id: missionId, run_id: 'r', revision: ++revision, met: null, clock_source: null, pending: 0, events: ids.map(status) };
}

describe('MissionTimeline', () => {
  it('tells subscribers when the events or the statuses change', () => {
    const statuses = new TimelineStatusStore();
    const timeline = new MissionTimeline(statuses, [ev('a')]);
    const listener = vi.fn();
    const unsubscribe = timeline.subscribe(listener);
    timeline.setEvents([ev('a'), ev('b')]);
    statuses.apply(payload('ift-14', ['a', 'b']));
    expect(listener).toHaveBeenCalledTimes(2);
    expect(timeline.events.map((e) => e.id)).toEqual(['a', 'b']);
    unsubscribe();
    timeline.setEvents([]);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('follows the dashboard mission, now and on every change', () => {
    let listener = () => {};
    const mission = {
      timeline: [ev('a')] as readonly TimelineEvent[],
      subscribeTimeline: (l: () => void) => {
        listener = l;
        return () => {};
      },
    } as unknown as CurrentMission;
    const timeline = new MissionTimeline(new TimelineStatusStore());
    timeline.followMission(mission);
    expect(timeline.events.map((e) => e.id)).toEqual(['a']);
    (mission as unknown as { timeline: TimelineEvent[] }).timeline = [ev('b')];
    listener();
    expect(timeline.events.map((e) => e.id)).toEqual(['b']);
  });
});

describe('MissionTimeline.followStatuses (overlays)', () => {
  const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

  it('reads the events of the mission a snapshot names, once for the same snapshot contents', async () => {
    const statuses = new TimelineStatusStore();
    const timeline = new MissionTimeline(statuses);
    const load = vi.fn(async () => [ev('a'), ev('b')]);
    timeline.followStatuses(load);
    expect(load).not.toHaveBeenCalled();

    statuses.apply(payload('ift-14', ['a', 'b']));
    statuses.apply(payload('ift-14', ['a', 'b']));
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith('ift-14');
    expect(timeline.events.map((e) => e.id)).toEqual(['a', 'b']);
  });

  it('reads again when the mission or its event ids change, and not when they already match', async () => {
    const statuses = new TimelineStatusStore();
    const timeline = new MissionTimeline(statuses, [ev('a'), ev('b')]);
    const load = vi.fn(async (id: string) => (id === 'ift-15' ? [ev('x')] : [ev('a'), ev('b'), ev('c')]));
    timeline.followStatuses(load);

    statuses.apply(payload('ift-14', ['a', 'b']));
    await flush();
    expect(load).not.toHaveBeenCalled();

    statuses.apply(payload('ift-14', ['a', 'b', 'c']));
    await flush();
    expect(timeline.events.map((e) => e.id)).toEqual(['a', 'b', 'c']);

    statuses.apply(payload('ift-15', ['x']));
    await flush();
    expect(load).toHaveBeenLastCalledWith('ift-15');
    expect(timeline.events.map((e) => e.id)).toEqual(['x']);
  });

  it('empties the events when no mission is followed, and when it has no file', async () => {
    const statuses = new TimelineStatusStore();
    const timeline = new MissionTimeline(statuses, [ev('a')]);
    timeline.followStatuses(async () => null);
    statuses.apply(payload(null, []));
    expect(timeline.events).toEqual([]);

    timeline.setEvents([ev('a')]);
    statuses.apply(payload('ift-16', ['q']));
    await flush();
    expect(timeline.events).toEqual([]);
  });

  it('ignores a read that a newer snapshot has overtaken', async () => {
    const statuses = new TimelineStatusStore();
    const timeline = new MissionTimeline(statuses);
    const resolvers = new Map<string, (events: TimelineEvent[]) => void>();
    timeline.followStatuses((id) => new Promise((resolve) => resolvers.set(id, resolve)));
    statuses.apply(payload('ift-14', ['a']));
    statuses.apply(payload('ift-15', ['b']));
    resolvers.get('ift-15')!([ev('b')]);
    await flush();
    resolvers.get('ift-14')!([ev('a')]);
    await flush();
    expect(timeline.events.map((e) => e.id)).toEqual(['b']);
  });

  it('tries again with the next snapshot after a failed read', async () => {
    const statuses = new TimelineStatusStore();
    const timeline = new MissionTimeline(statuses);
    const load = vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValue([ev('a')]);
    timeline.followStatuses(load);
    statuses.apply(payload('ift-14', ['a']));
    await flush();
    expect(timeline.events).toEqual([]);
    statuses.apply(payload('ift-14', ['a']));
    await flush();
    expect(timeline.events.map((e) => e.id)).toEqual(['a']);
  });

  it('reads at once when a snapshot is already held', async () => {
    const statuses = new TimelineStatusStore();
    statuses.apply(payload('ift-14', ['a']));
    const timeline = new MissionTimeline(statuses);
    timeline.followStatuses(async () => [ev('a')]);
    await flush();
    expect(timeline.events.map((e) => e.id)).toEqual(['a']);
  });
});
