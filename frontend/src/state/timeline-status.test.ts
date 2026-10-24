import { describe, it, expect, vi } from 'vitest';
import fc from 'fast-check';
import { TimelineStatusStore } from './timeline-status';
import type { TimelineEventStatus, TimelineStatusPayload } from '../core/types';

function eventStatus(id: string, status: TimelineEventStatus['status'] = 'not_yet'): TimelineEventStatus {
  return { event_id: id, status, source: 'auto', evidence: null, met: null, updated_at: '2026-10-24T00:00:00Z' };
}

function payload(revision: number, events: TimelineEventStatus[] = [], pending = 0): TimelineStatusPayload {
  return { mission_id: 'ift-14', run_id: 'run-1', revision, met: null, clock_source: null, pending, events };
}

describe('TimelineStatusStore', () => {
  it('is empty before the first snapshot', () => {
    const store = new TimelineStatusStore();
    expect(store.snapshot).toBeNull();
    expect(store.pending).toBe(0);
    expect(store.status('liftoff')).toBeUndefined();
  });

  it('takes timeline_status messages and ignores every other type', () => {
    const store = new TimelineStatusStore();
    const listener = vi.fn();
    store.subscribe(listener);
    store.handleMessage({ type: 'status', payload: { status: 'running' } as never });
    expect(listener).not.toHaveBeenCalled();
    store.handleMessage({ type: 'timeline_status', payload: payload(5, [eventStatus('liftoff', 'pending')], 1) });
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.status('liftoff')?.status).toBe('pending');
    expect(store.pending).toBe(1);
  });

  it('drops a snapshot older than the one held (a late GET after a WebSocket update)', () => {
    const store = new TimelineStatusStore();
    expect(store.apply(payload(10, [eventStatus('a', 'success')]))).toBe(true);
    expect(store.apply(payload(9, [eventStatus('a', 'not_yet')]))).toBe(false);
    expect(store.apply(payload(10, [eventStatus('a', 'not_yet')]))).toBe(false);
    expect(store.status('a')?.status).toBe('success');
  });

  it('a snapshot replaces the previous one whole: events no longer followed are gone', () => {
    const store = new TimelineStatusStore();
    store.apply(payload(1, [eventStatus('a'), eventStatus('b')]));
    store.apply(payload(2, [eventStatus('b')]));
    expect(store.status('a')).toBeUndefined();
    expect(store.snapshot?.events.map((e) => e.event_id)).toEqual(['b']);
  });

  it('loads the current snapshot, and survives an unreachable backend', async () => {
    const store = new TimelineStatusStore();
    await store.load(async () => payload(3, [eventStatus('x')]));
    expect(store.snapshot?.revision).toBe(3);
    await store.load(async () => {
      throw new TypeError('Failed to fetch');
    });
    expect(store.snapshot?.revision).toBe(3);
  });

  it('unsubscribed listeners hear nothing more', () => {
    const store = new TimelineStatusStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    unsubscribe();
    store.apply(payload(1));
    expect(listener).not.toHaveBeenCalled();
  });

  it('whatever order snapshots arrive in, the newest one is held', () => {
    fc.assert(
      fc.property(fc.uniqueArray(fc.integer({ min: 0, max: 1000 }), { minLength: 1, maxLength: 20 }), (revisions) => {
        const store = new TimelineStatusStore();
        for (const r of revisions) store.apply(payload(r));
        expect(store.snapshot?.revision).toBe(Math.max(...revisions));
      })
    );
  });
});
