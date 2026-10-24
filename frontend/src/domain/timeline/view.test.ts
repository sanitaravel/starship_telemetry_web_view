import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { TimelineEvent, TimelineVehicle } from './types';
import { TIMELINE_STATUSES, TIMELINE_VEHICLES } from './types';
import {
  currentEventIndex,
  formatClock,
  formatDuration,
  nextEventIndex,
  pendingText,
  STATUS_COLORS,
  STATUS_LABELS,
  statusTooltip,
  timelineEventsForVehicle,
} from './view';

function ev(id: string, t: number, vehicle: TimelineVehicle = 'both'): TimelineEvent {
  return { id, t, event: id, vehicle, auto: { kind: 'manual' } };
}

const EVENTS = [ev('load', -600), ev('go', -30), ev('t0-a', 0), ev('t0-b', 0), ev('maxq', 58), ev('meco', 158)];

describe('current and next event', () => {
  it('is the last event at or before the MET, and the next one after it', () => {
    expect(currentEventIndex(EVENTS, 60)).toBe(4);
    expect(nextEventIndex(EVENTS, 60)).toBe(5);
    expect(currentEventIndex(EVENTS, 58)).toBe(4);
    expect(nextEventIndex(EVENTS, 58)).toBe(5);
  });

  it('takes the second of two events at T-0', () => {
    expect(currentEventIndex(EVENTS, 0)).toBe(3);
    expect(nextEventIndex(EVENTS, 0)).toBe(4);
  });

  it('counts down before liftoff: no current event before the first, the first is next', () => {
    expect(currentEventIndex(EVENTS, -700)).toBeNull();
    expect(nextEventIndex(EVENTS, -700)).toBe(0);
    expect(currentEventIndex(EVENTS, -31)).toBe(0);
    expect(nextEventIndex(EVENTS, -31)).toBe(1);
  });

  it('has no next event after the last, and none at all without a MET', () => {
    expect(nextEventIndex(EVENTS, 1000)).toBeNull();
    expect(currentEventIndex(EVENTS, 1000)).toBe(5);
    expect(currentEventIndex(EVENTS, null)).toBeNull();
    expect(nextEventIndex(EVENTS, null)).toBeNull();
  });

  it('puts the current event right before the next one for any sorted events and MET', () => {
    fc.assert(
      fc.property(fc.array(fc.integer({ min: -3600, max: 7200 }), { maxLength: 30 }), fc.integer({ min: -4000, max: 8000 }), (ts, met) => {
        const events = [...ts].sort((a, b) => a - b).map((t, i) => ev(`e${i}`, t));
        const current = currentEventIndex(events, met);
        const next = nextEventIndex(events, met);
        if (current !== null) expect(events[current].t).toBeLessThanOrEqual(met);
        if (next !== null) expect(events[next].t).toBeGreaterThan(met);
        expect(next === null ? events.length : next).toBe((current ?? -1) + 1);
      }),
    );
  });
});

describe('formatting', () => {
  it('writes the time to go as m:ss, h:mm:ss from an hour, rounded up', () => {
    expect(formatDuration(0)).toBe('0:00');
    expect(formatDuration(0.2)).toBe('0:01');
    expect(formatDuration(75)).toBe('1:15');
    expect(formatDuration(3600)).toBe('1:00:00');
    expect(formatDuration(3725)).toBe('1:02:05');
  });

  it('writes the clock as T-/T+, or a dash while unknown', () => {
    expect(formatClock(-751)).toBe('T-00:12:31');
    expect(formatClock(0)).toBe('T+00:00:00');
    expect(formatClock(98.4)).toBe('T+00:01:38');
    expect(formatClock(null)).toBe('—');
  });

  it('counts the events waiting for a person', () => {
    expect(pendingText(0)).toBe('');
    expect(pendingText(1)).toBe('1 needs confirmation');
    expect(pendingText(2)).toBe('2 need confirmation');
  });

  it('has a label and a distinct colour for the statuses that are not grey', () => {
    for (const status of TIMELINE_STATUSES) expect(STATUS_LABELS[status]).toBeTruthy();
    const colored = TIMELINE_STATUSES.filter((s) => s !== 'not_yet' && s !== 'skipped').map((s) => STATUS_COLORS[s]);
    expect(new Set(colored).size).toBe(colored.length);
  });

  it('describes the status, who set it, why and when', () => {
    const text = statusTooltip(ev('meco', 158), { status: 'partial', source: 'human', evidence: '32 of 33 engines', met: 160.2 });
    expect(text).toBe('meco\nPartial success (set by hand)\n32 of 33 engines\nDecided at T+00:02:40');
    expect(statusTooltip(ev('meco', 158), { status: 'not_yet', source: 'auto', evidence: null, met: null })).toBe(
      'meco\nNot yet (automatic)',
    );
  });
});

describe('timelineEventsForVehicle', () => {
  const events = [
    ev('countdown', -30, 'both'),
    ev('liftoff', 0, 'both'),
    ev('sh-boostback', 145, 'superheavy'),
    ev('ss-ignition', 140, 'starship'),
    ev('ss-seco', 500, 'starship'),
    ev('sh-landing', 400, 'superheavy'),
    ev('hot-staging', 141, 'both'),
  ];
  const ids = (list: TimelineEvent[]) => list.map((e) => e.id);

  it('never draws the countdown', () => {
    expect(ids(timelineEventsForVehicle(events, 'superheavy', null))).not.toContain('countdown');
    expect(ids(timelineEventsForVehicle(events, 'starship', 142))).not.toContain('countdown');
  });

  it('shows Super Heavy graphs the Super Heavy and shared events, whenever they happen', () => {
    expect(ids(timelineEventsForVehicle(events, 'superheavy', 142))).toEqual(['liftoff', 'sh-boostback', 'sh-landing', 'hot-staging']);
  });

  it('shows Starship graphs the Super Heavy events before separation and the Starship ones after', () => {
    // Separation at T+142: ss-ignition (140) is before it, sh-boostback (145) after
    expect(ids(timelineEventsForVehicle(events, 'starship', 142))).toEqual(['liftoff', 'ss-seco', 'hot-staging']);
    expect(ids(timelineEventsForVehicle(events, 'starship', 1000))).toEqual(['liftoff', 'sh-boostback', 'sh-landing', 'hot-staging']);
  });

  it('shows Starship graphs every event while separation is unknown', () => {
    expect(ids(timelineEventsForVehicle(events, 'starship', null))).toEqual([
      'liftoff',
      'sh-boostback',
      'ss-ignition',
      'ss-seco',
      'sh-landing',
      'hot-staging',
    ]);
  });

  it('keeps shared events everywhere and the vehicles apart after separation', () => {
    fc.assert(
      fc.property(
        fc.array(fc.record({ t: fc.integer({ min: -100, max: 1000 }), vehicle: fc.constantFrom(...TIMELINE_VEHICLES) }), { maxLength: 20 }),
        fc.option(fc.integer({ min: 0, max: 1000 }), { nil: null }),
        (rows, separation) => {
          const list = rows.map((r, i) => ev(`e${i}`, r.t, r.vehicle));
          const sh = timelineEventsForVehicle(list, 'superheavy', separation);
          const ss = timelineEventsForVehicle(list, 'starship', separation);
          for (const e of [...sh, ...ss]) expect(e.t).toBeGreaterThanOrEqual(0);
          for (const e of list.filter((e) => e.vehicle === 'both' && e.t >= 0)) {
            expect(sh).toContain(e);
            expect(ss).toContain(e);
          }
          for (const e of sh) expect(e.vehicle).not.toBe('starship');
          if (separation !== null) {
            for (const e of ss.filter((e) => e.vehicle === 'starship')) expect(e.t).toBeGreaterThanOrEqual(separation);
            for (const e of ss.filter((e) => e.vehicle === 'superheavy')) expect(e.t).toBeLessThan(separation);
          }
        },
      ),
    );
  });
});
