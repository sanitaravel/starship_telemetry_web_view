/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { TimelinePanel, AUTO_SCROLL_PAUSE_MS, EMPTY_TEXT } from './timeline-panel';
import { MissionTimeline } from '../../state/mission-timeline';
import { TimelineStatusStore } from '../../state/timeline-status';
import { StateManager } from '../../state/state-manager';
import type { TelemetryRecord, TimelineEventStatus, TimelineStatusPayload, TimelineStatusValue } from '../../core/types';
import type { TimelineEvent } from '../../domain/timeline/types';
import { STATUS_LABELS } from '../../domain/timeline/view';
import { closeOverrideMenu } from './override-menu';

function ev(id: string, t: number, event = id, vehicle: TimelineEvent['vehicle'] = 'both'): TimelineEvent {
  return { id, t, event, vehicle, auto: { kind: 'manual' } };
}

const EVENTS = [
  ev('go', -30, 'Go for launch'),
  ev('t0-a', 0, 'Booster engine start', 'superheavy'),
  ev('t0-b', 0, 'Liftoff'),
  ev('maxq', 58, 'Max Q', 'starship'),
  ev('meco', 158, 'MECO'),
];

function status(
  id: string,
  value: TimelineStatusValue = 'not_yet',
  extra: Partial<TimelineEventStatus> = {},
): TimelineEventStatus {
  return { event_id: id, status: value, source: 'auto', evidence: null, met: null, updated_at: '2026-10-24T00:00:00Z', ...extra };
}

let revision = 0;
function payload(events: TimelineEventStatus[], extra: Partial<TimelineStatusPayload> = {}): TimelineStatusPayload {
  return {
    mission_id: 'ift-14',
    run_id: 'run-1',
    revision: ++revision,
    met: null,
    clock_source: null,
    pending: events.filter((e) => e.status === 'pending').length,
    events,
    ...extra,
  };
}

function record(met: string | null): TelemetryRecord {
  return {
    sequence_number: 1,
    mission_elapsed_time: met,
    mission_elapsed_time_raw: met,
    speed_left: { value: null, unit: null, status: 'unavailable' },
    speed_right: { value: null, unit: null, status: 'unavailable' },
    altitude_left: { value: null, unit: null, status: 'unavailable' },
    altitude_right: { value: null, unit: null, status: 'unavailable' },
    stage_left_label: null,
    stage_right_label: null,
    stage_separation_text: null,
    stage_assignment_left: 'super_heavy',
    stage_assignment_right: 'super_heavy',
    separation_state: 'pre_separation',
    t_zero_found: true,
    stage_sep_found: false,
    starship_engines: {},
    superheavy_engines: {},
    detection_accuracy: { starship: 0, superheavy: 0 },
    timestamp: 1700000000000,
  };
}

let container: HTMLElement;
let statuses: TimelineStatusStore;
let timeline: MissionTimeline;
let stateManager: StateManager;

function mount(options: ConstructorParameters<typeof TimelinePanel>[3] = {}): TimelinePanel {
  return new TimelinePanel(container, timeline, stateManager, options);
}

const rows = () => [...container.querySelectorAll<HTMLElement>('.timeline-row')];
const row = (id: string) => container.querySelector<HTMLElement>(`.timeline-row[data-event-id="${id}"]`)!;
const circle = (id: string) => row(id).querySelector<HTMLElement>('.timeline-circle')!;
const menu = () => document.querySelector<HTMLElement>('.timeline-menu');
const setMet = (met: string) => stateManager.handleMessage({ type: 'telemetry', payload: record(met) });

beforeEach(() => {
  // The status menu is a singleton living in the body; only the panel's container is replaced
  container?.remove();
  container = document.createElement('div');
  document.body.appendChild(container);
  statuses = new TimelineStatusStore();
  timeline = new MissionTimeline(statuses, EVENTS);
  stateManager = new StateManager();
});

afterEach(() => {
  closeOverrideMenu();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('TimelinePanel rows', () => {
  it('lists the events in order with time, text, vehicle tag and a status circle', () => {
    mount();
    expect(rows().map((r) => r.dataset.eventId)).toEqual(['go', 't0-a', 't0-b', 'maxq', 'meco']);
    const first = row('go');
    expect(first.querySelector('.timeline-row__time')!.textContent).toBe('T-00:00:30');
    expect(first.querySelector('.timeline-row__text')!.textContent).toBe('Go for launch');
    expect(first.querySelector('.timeline-row__vehicle')!.textContent).toBe('ALL');
    expect(row('t0-a').querySelector('.timeline-row__vehicle')!.textContent).toBe('SH');
    expect(row('maxq').querySelector('.timeline-row__vehicle')!.textContent).toBe('SS');
    expect(row('maxq').querySelector('.timeline-row__time')!.textContent).toBe('T+00:00:58');
    expect(circle('go').dataset.status).toBe('not_yet');
  });

  it('shows the empty state without a timeline', () => {
    timeline.setEvents([]);
    mount();
    expect(container.querySelector('.timeline-panel__empty')!.textContent).toBe(EMPTY_TEXT);
    expect(container.querySelector<HTMLElement>('.timeline-panel__empty')!.hidden).toBe(false);
    expect(container.querySelector<HTMLElement>('.timeline-panel__list')!.hidden).toBe(true);
    timeline.setEvents(EVENTS);
    expect(container.querySelector<HTMLElement>('.timeline-panel__empty')!.hidden).toBe(true);
    expect(rows()).toHaveLength(5);
  });

  it('draws each status, with the evidence and decision time in the tooltip, and does not dim past events', () => {
    mount();
    setMet('T+00:01:30');
    statuses.apply(
      payload([
        status('go', 'success', { evidence: 'clock', met: -30 }),
        status('t0-a', 'partial', { source: 'human' }),
        status('t0-b', 'failure'),
        status('maxq', 'pending'),
        status('meco', 'skipped'),
      ]),
    );
    expect(['go', 't0-a', 't0-b', 'maxq', 'meco'].map((id) => circle(id).dataset.status)).toEqual([
      'success',
      'partial',
      'failure',
      'pending',
      'skipped',
    ]);
    expect(circle('go').title).toContain('clock');
    expect(circle('go').title).toContain('Decided at T-00:00:30');
    expect(circle('t0-a').querySelector('.timeline-circle__badge')).not.toBeNull();
    expect(circle('go').querySelector('.timeline-circle__badge')).toBeNull();
    expect(row('go').style.opacity).toBe('');
    expect(row('go').classList.contains('timeline-row--current')).toBe(false);
  });

  it('keeps the circle element when a status changes, so focus survives', () => {
    mount();
    const before = circle('maxq');
    statuses.apply(payload([status('maxq', 'success')]));
    expect(circle('maxq')).toBe(before);
    expect(before.dataset.status).toBe('success');
  });
});

describe('TimelinePanel header and current event', () => {
  it('highlights the last event at or before the MET, the second of two at T-0', () => {
    mount();
    setMet('T+00:00:00');
    expect(rows().filter((r) => r.classList.contains('timeline-row--current')).map((r) => r.dataset.eventId)).toEqual(['t0-b']);
    expect(row('t0-b').getAttribute('aria-current')).toBe('step');
    setMet('T+00:01:00');
    expect(rows().filter((r) => r.classList.contains('timeline-row--current')).map((r) => r.dataset.eventId)).toEqual(['maxq']);
    expect(row('t0-b').hasAttribute('aria-current')).toBe(false);
  });

  it('shows the MET, the next event and the time to it', () => {
    mount();
    setMet('T+00:00:30');
    expect(container.querySelector('.timeline-panel__clock')!.textContent).toBe('T+00:00:30');
    expect(container.querySelector('.timeline-panel__next')!.textContent).toBe('next: Max Q in 0:28');
    setMet('T+00:03:00');
    expect(container.querySelector('.timeline-panel__next')!.textContent).toBe('No more events');
  });

  it('counts down before liftoff from the backend clock', () => {
    mount();
    statuses.apply(payload([], { met: -90, clock_source: 'countdown' }));
    expect(container.querySelector('.timeline-panel__clock')!.textContent).toBe('T-00:01:30');
    expect(container.querySelector('.timeline-panel__next')!.textContent).toBe('next: Go for launch in 1:00');
    expect(row('go').classList.contains('timeline-row--current')).toBe(false);
    statuses.apply(payload([], { met: -20, clock_source: 'countdown' }));
    expect(row('go').classList.contains('timeline-row--current')).toBe(true);
    expect(container.querySelector('.timeline-panel__next')!.textContent).toBe('next: Booster engine start in 0:20');
  });

  it('marks a countdown hold and drops the mark when it resumes', () => {
    mount();
    statuses.apply(payload([], { met: -40, clock_source: 'countdown', hold: true }));
    expect(container.querySelector('.timeline-panel__clock')!.textContent).toBe('T-00:00:40 HOLD');
    statuses.apply(payload([], { met: -39, clock_source: 'countdown', hold: false }));
    expect(container.querySelector('.timeline-panel__clock')!.textContent).toBe('T-00:00:39');
  });

  it('shows a dash while no MET is known', () => {
    mount();
    expect(container.querySelector('.timeline-panel__clock')!.textContent).toBe('—');
    expect(container.querySelector('.timeline-panel__next')!.textContent).toBe('Waiting for the clock');
    expect(container.querySelector('.timeline-row--current')).toBeNull();
  });

  it('takes the later of the telemetry MET and the backend MET', () => {
    mount();
    statuses.apply(payload([], { met: 100, clock_source: 'telemetry' }));
    setMet('T+00:01:30');
    expect(container.querySelector('.timeline-panel__clock')!.textContent).toBe('T+00:01:40');
    setMet('T+00:02:00');
    expect(container.querySelector('.timeline-panel__clock')!.textContent).toBe('T+00:02:00');
  });

  it('counts the events that need confirmation', () => {
    mount();
    const pending = container.querySelector<HTMLElement>('.timeline-panel__pending')!;
    expect(pending.hidden).toBe(true);
    statuses.apply(payload([status('go', 'pending'), status('maxq', 'pending')]));
    expect(pending.hidden).toBe(false);
    expect(pending.textContent).toBe('2 need confirmation');
    statuses.apply(payload([status('go', 'pending')]));
    expect(pending.textContent).toBe('1 needs confirmation');
  });
});

describe('TimelinePanel auto-scroll', () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: new Date('2026-10-24T12:00:00Z') });
    // 20 px rows in a 60 px list
    vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('timeline-row') ? EVENTS.findIndex((e) => e.id === this.dataset.eventId) * 20 : 0;
    });
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('timeline-row') ? 20 : 0;
    });
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('timeline-panel__list') ? 60 : 0;
    });
  });

  const list = () => container.querySelector<HTMLElement>('.timeline-panel__list')!;

  it('scrolls the current event into view', () => {
    mount();
    setMet('T+00:02:40');
    // MECO is the 5th row, at 80..100; centred in the 60 px list
    expect(list().scrollTop).toBe(60);
  });

  it('leaves the list alone for 10 s after the user scrolled it, then catches up', () => {
    mount();
    list().dispatchEvent(new Event('wheel'));
    setMet('T+00:02:40');
    expect(list().scrollTop).toBe(0);

    vi.advanceTimersByTime(AUTO_SCROLL_PAUSE_MS - 1);
    setMet('T+00:02:41');
    expect(list().scrollTop).toBe(0);

    vi.advanceTimersByTime(2);
    setMet('T+00:02:42');
    expect(list().scrollTop).toBe(60);
  });

  it('counts a key press or a pointer press in the list as scrolling by the user', () => {
    for (const type of ['keydown', 'pointerdown', 'touchmove']) {
      container.innerHTML = '';
      const panel = mount();
      list().dispatchEvent(new Event(type));
      setMet('T+00:02:40');
      expect(list().scrollTop).toBe(0);
      panel.destroy();
      stateManager = new StateManager();
    }
  });
});

describe('TimelinePanel status menu', () => {
  it('opens under a circle with the six statuses, the current one checked, and no back-to-auto for an automatic status', () => {
    mount();
    statuses.apply(payload([status('maxq', 'success')]));
    circle('maxq').click();
    const items = [...menu()!.querySelectorAll<HTMLElement>('[role="menuitemradio"]')];
    expect(items.map((i) => i.textContent)).toEqual(Object.values(STATUS_LABELS));
    expect(items.filter((i) => i.getAttribute('aria-checked') === 'true').map((i) => i.dataset.status)).toEqual(['success']);
    expect(menu()!.querySelector('[role="menuitem"]')).toBeNull();
    expect(menu()!.getAttribute('role')).toBe('menu');
    expect(circle('maxq').getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(items.find((i) => i.dataset.status === 'success'));
  });

  it('sets the chosen status through the API, then takes the snapshot', async () => {
    const override = vi.fn(async (eventId: string, value: TimelineStatusValue | null) =>
      payload([status(eventId, value ?? 'not_yet', { source: 'human' })]),
    );
    mount({ override });
    circle('maxq').click();
    menu()!.querySelector<HTMLElement>('[data-status="failure"]')!.click();

    expect(override).toHaveBeenCalledWith('maxq', 'failure');
    expect(menu()!.hidden).toBe(true);
    expect(row('maxq').classList.contains('timeline-row--saving')).toBe(true);
    await vi.waitFor(() => expect(circle('maxq').dataset.status).toBe('failure'));
    expect(row('maxq').classList.contains('timeline-row--saving')).toBe(false);
    expect(circle('maxq').dataset.source).toBe('human');
    expect(document.activeElement).toBe(circle('maxq'));
  });

  it('offers back to auto only for a human status, and sends null', async () => {
    const override = vi.fn(async () => payload([status('maxq', 'not_yet')]));
    mount({ override });
    statuses.apply(payload([status('maxq', 'failure', { source: 'human' })]));
    circle('maxq').click();
    const back = menu()!.querySelector<HTMLElement>('[role="menuitem"]')!;
    expect(back.textContent).toBe('Back to auto');
    back.click();
    expect(override).toHaveBeenCalledWith('maxq', null);
    await vi.waitFor(() => expect(row('maxq').classList.contains('timeline-row--saving')).toBe(false));
  });

  it('moves with the arrow keys, Home and End, and picks with the keyboard', () => {
    const override = vi.fn(async () => payload([]));
    mount({ override });
    circle('maxq').click();
    const items = [...menu()!.querySelectorAll<HTMLElement>('.timeline-menu__item')];
    const press = (key: string) => menu()!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(items[0]);
    press('ArrowDown');
    expect(document.activeElement).toBe(items[1]);
    press('End');
    expect(document.activeElement).toBe(items[items.length - 1]);
    press('ArrowDown');
    expect(document.activeElement).toBe(items[0]);
    press('ArrowUp');
    expect(document.activeElement).toBe(items[items.length - 1]);
    press('Home');
    press('ArrowDown');
    // A button activates on Enter and Space; jsdom turns that into a click
    (document.activeElement as HTMLElement).click();
    expect(override).toHaveBeenCalledWith('maxq', 'pending');
  });

  it('closes on Escape and returns focus to the circle, without calling the API', () => {
    const override = vi.fn();
    mount({ override });
    circle('maxq').click();
    menu()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(menu()!.hidden).toBe(true);
    expect(document.activeElement).toBe(circle('maxq'));
    expect(circle('maxq').getAttribute('aria-expanded')).toBe('false');
    expect(override).not.toHaveBeenCalled();
  });

  it('closes when its circle is clicked again', () => {
    mount();
    circle('maxq').click();
    circle('maxq').click();
    expect(menu()!.hidden).toBe(true);
  });

  it('shows an inline message when the API fails, and clears it on the next try', async () => {
    const override = vi
      .fn<(id: string, status: TimelineStatusValue | null) => Promise<TimelineStatusPayload>>()
      .mockRejectedValueOnce(new Error('Setting the status of maxq failed (500)'))
      .mockResolvedValueOnce(payload([status('maxq', 'success', { source: 'human' })]));
    mount({ override });
    const errorLine = container.querySelector<HTMLElement>('.timeline-panel__error')!;
    expect(errorLine.hidden).toBe(true);

    circle('maxq').click();
    menu()!.querySelector<HTMLElement>('[data-status="success"]')!.click();
    await vi.waitFor(() => expect(errorLine.hidden).toBe(false));
    expect(errorLine.textContent).toContain('Max Q');
    expect(errorLine.textContent).toContain('500');
    expect(errorLine.getAttribute('role')).toBe('alert');
    // Not changed on the screen: the status only moves with the next snapshot
    expect(circle('maxq').dataset.status).toBe('not_yet');

    circle('maxq').click();
    menu()!.querySelector<HTMLElement>('[data-status="success"]')!.click();
    expect(errorLine.hidden).toBe(true);
    await vi.waitFor(() => expect(circle('maxq').dataset.status).toBe('success'));
  });

  it('says the backend is unreachable when the request never got an answer', async () => {
    mount({ override: vi.fn().mockRejectedValue(new TypeError('Failed to fetch')) });
    circle('maxq').click();
    menu()!.querySelector<HTMLElement>('[data-status="success"]')!.click();
    const errorLine = container.querySelector<HTMLElement>('.timeline-panel__error')!;
    await vi.waitFor(() => expect(errorLine.hidden).toBe(false));
    expect(errorLine.textContent).toContain('backend unreachable');
  });
});

describe('TimelinePanel without chrome (overlays)', () => {
  it('is read-only: no buttons, no menu, no OBS button, no title, no override call', () => {
    const override = vi.fn();
    mount({ chrome: false, override });
    expect(container.querySelectorAll('button, select, input, [tabindex]')).toHaveLength(0);
    expect(container.querySelector('.timeline-panel__title')).toBeNull();
    expect(container.querySelector('.obs-btn')).toBeNull();
    expect(rows()).toHaveLength(5);
    circle('maxq').click();
    // The menu singleton may exist from earlier tests; it must not be open
    expect(menu()?.hidden ?? true).toBe(true);
    expect(override).not.toHaveBeenCalled();
  });

  it('still shows the statuses, the clock and the next event', () => {
    mount({ chrome: false });
    setMet('T+00:00:30');
    statuses.apply(payload([status('maxq', 'pending')]));
    expect(circle('maxq').dataset.status).toBe('pending');
    expect(container.querySelector('.timeline-panel__clock')!.textContent).toBe('T+00:00:30');
    expect(container.querySelector('.timeline-panel__next')!.textContent).toContain('Max Q');
  });
});

describe('TimelinePanel compact ticker', () => {
  it('shows only the current event and the next one with the time to it', () => {
    mount({ chrome: false, compact: true });
    setMet('T+00:00:30');
    expect(rows().map((r) => r.dataset.eventId)).toEqual(['t0-b', 'maxq']);
    expect(row('maxq').querySelector('.timeline-row__eta')!.textContent).toBe('in 0:28');
    expect(row('t0-b').querySelector('.timeline-row__eta')!.textContent).toBe('');
    expect(container.querySelector('.timeline-panel__header')).toBeNull();

    setMet('T+00:00:40');
    expect(row('maxq').querySelector('.timeline-row__eta')!.textContent).toBe('in 0:18');
    setMet('T+00:01:00');
    expect(rows().map((r) => r.dataset.eventId)).toEqual(['maxq', 'meco']);
  });

  it('shows the first event while the MET is unknown, and only the last one at the end', () => {
    mount({ chrome: false, compact: true });
    expect(rows().map((r) => r.dataset.eventId)).toEqual(['go']);
    setMet('T+00:10:00');
    expect(rows().map((r) => r.dataset.eventId)).toEqual(['meco']);
  });

  it('keeps the status of a row across the MET moving', () => {
    mount({ chrome: false, compact: true });
    setMet('T+00:00:30');
    statuses.apply(payload([status('maxq', 'pending'), status('t0-b', 'success')]));
    expect(circle('maxq').dataset.status).toBe('pending');
    setMet('T+00:00:31');
    expect(circle('maxq').dataset.status).toBe('pending');
    expect(circle('t0-b').dataset.status).toBe('success');
  });
});
