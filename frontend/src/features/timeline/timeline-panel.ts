import type { StateManager } from '../../state/state-manager';
import type { MissionTimeline } from '../../state/mission-timeline';
import type { TimelineStatusPayload, TimelineStatusValue } from '../../core/types';
import { overrideTimelineStatus } from '../../api/timeline';
import { parseMETToSeconds } from '../../core/met';
import { createLogger } from '../../core/logger';
import type { TimelineEvent } from '../../domain/timeline/types';
import { formatTimelineTime } from '../../domain/timeline/parse';
import {
  currentEventIndex,
  formatClock,
  formatDuration,
  nextEventIndex,
  NO_STATUS,
  pendingText,
  statusTooltip,
  VEHICLE_TAGS,
} from '../../domain/timeline/view';
import { buildOverlayUrl } from '../../domain/overlay/snapshot';
import { overlayWidget } from '../../domain/overlay/widgets';
import { createObsButton, overlayBaseUrl, toggleObsPopover } from '../../ui/obs-popover';
import { closeOverrideMenu, toggleOverrideMenu } from './override-menu';
import { fillStatusCircle } from './status-circle';

const logger = createLogger('timeline-panel');

export interface TimelinePanelOptions {
  /**
   * False leaves out the title, OBS button, the status menu and the
   * circles' buttons (overlays: read-only). Default true.
   */
  chrome?: boolean;
  /** Only the current event and the next one, as a ticker (the `timeline-next` overlay). Default false. */
  compact?: boolean;
  /** Sets or clears a person's status; swapped out in tests. Default the backend call. */
  override?: (eventId: string, status: TimelineStatusValue | null) => Promise<TimelineStatusPayload>;
}

/** After the user scrolls the list themselves, it is left alone this long. */
export const AUTO_SCROLL_PAUSE_MS = 10_000;

/** What a row last showed, to leave unchanged rows alone. */
interface Row {
  element: HTMLLIElement;
  circle: HTMLElement;
  eta: HTMLElement;
  shown: string;
}

/** The text of the events shown when no timeline is entered. */
export const EMPTY_TEXT = 'No timeline. Add one with the Timeline button in the engine settings.';

/**
 * The mission timeline: every planned event in order with its status circle
 * (not yet, needs confirmation, success, partial, failure, skipped; "H" when
 * a person set it), the current event highlighted, the time to the next one
 * and how many events wait for a person. Clicking a circle opens a menu to
 * set the status by hand or hand it back to automatic; the new status comes
 * back through the backend's next snapshot. Past events are not dimmed.
 *
 * The MET is the latest of the telemetry's and the backend's furthest one
 * (the countdown clock, before liftoff).
 */
export class TimelinePanel {
  private readonly chrome: boolean;
  private readonly compact: boolean;
  private readonly override: NonNullable<TimelinePanelOptions['override']>;

  private readonly clock: HTMLElement;
  private readonly next: HTMLElement;
  private readonly pending: HTMLElement;
  private readonly errorLine: HTMLElement;
  private readonly list: HTMLOListElement;
  private readonly empty: HTMLElement;

  private rows = new Map<string, Row>();
  /** Which events the rows were built for. */
  private builtFor = '';
  private currentId: string | null = null;
  private lastMet: number | null | undefined = undefined;
  private lastUserScrollAt = Number.NEGATIVE_INFINITY;
  private unsubscribers: (() => void)[] = [];

  constructor(
    private readonly container: HTMLElement,
    private readonly timeline: MissionTimeline,
    private readonly stateManager: StateManager,
    options: TimelinePanelOptions = {},
  ) {
    this.chrome = options.chrome ?? true;
    this.compact = options.compact ?? false;
    this.override = options.override ?? overrideTimelineStatus;

    container.innerHTML = '';
    container.classList.add('timeline-panel');
    container.classList.toggle('timeline-panel--compact', this.compact);
    container.classList.toggle('timeline-panel--overlay', !this.chrome);

    this.clock = document.createElement('span');
    this.clock.className = 'timeline-panel__clock';
    this.clock.setAttribute('aria-label', 'Mission elapsed time');
    this.next = document.createElement('span');
    this.next.className = 'timeline-panel__next';
    this.pending = document.createElement('span');
    this.pending.className = 'timeline-panel__pending';
    this.errorLine = document.createElement('p');
    this.errorLine.className = 'timeline-panel__error';
    this.errorLine.setAttribute('role', 'alert');
    this.errorLine.hidden = true;
    this.list = document.createElement('ol');
    this.list.className = 'timeline-panel__list';
    this.empty = document.createElement('p');
    this.empty.className = 'timeline-panel__empty';
    this.empty.textContent = EMPTY_TEXT;

    if (!this.compact) container.appendChild(this.buildHeader());
    if (this.chrome) {
      this.list.tabIndex = 0;
      this.list.setAttribute('aria-label', 'Timeline events');
      container.appendChild(this.errorLine);
    }
    container.append(this.list, this.empty);

    // Only the user's own scrolling pauses the automatic one
    for (const type of ['wheel', 'touchmove', 'pointerdown', 'keydown']) {
      this.list.addEventListener(type, () => {
        this.lastUserScrollAt = Date.now();
      });
    }

    this.unsubscribers.push(
      timeline.subscribe(() => this.update(true)),
      stateManager.subscribe(() => this.update(false)),
    );
    this.update(true);
  }

  /** Stops following the timeline and the state. */
  destroy(): void {
    for (const unsubscribe of this.unsubscribers) unsubscribe();
    this.unsubscribers = [];
    closeOverrideMenu();
  }

  private buildHeader(): HTMLElement {
    const header = document.createElement('div');
    header.className = 'timeline-panel__header';

    const top = document.createElement('div');
    top.className = 'timeline-panel__top';
    if (this.chrome) {
      const title = document.createElement('h2');
      title.className = 'timeline-panel__title';
      title.textContent = 'Mission timeline';
      top.appendChild(title);
    }
    top.appendChild(this.clock);
    if (this.chrome) top.appendChild(this.buildObsButton());

    const summary = document.createElement('div');
    summary.className = 'timeline-panel__summary';
    summary.append(this.next, this.pending);

    header.append(top, summary);
    return header;
  }

  /** OBS button offering the full list and the current-and-next ticker (neither has settings). */
  private buildObsButton(): HTMLButtonElement {
    const button = createObsButton('Mission timeline');
    button.classList.add('timeline-panel__obs');
    button.addEventListener('click', () =>
      toggleObsPopover(button, {
        title: 'Mission timeline',
        widgets: [overlayWidget('timeline'), overlayWidget('timeline-next')],
        selected: 'timeline',
        urlFor: (widget) => buildOverlayUrl(overlayBaseUrl(), widget, { v: 1 }),
      }),
    );
    return button;
  }

  /** The MET now: the latest of the telemetry's and the backend's furthest one; null when neither is known. */
  private currentMet(): number | null {
    const record = this.stateManager.getState().latestTelemetry;
    const fromRecord = record?.mission_elapsed_time ? parseMETToSeconds(record.mission_elapsed_time) : null;
    const fromBackend = this.timeline.statuses.snapshot?.met ?? null;
    if (fromRecord === null) return fromBackend;
    return fromBackend === null ? fromRecord : Math.max(fromRecord, fromBackend);
  }

  private update(timelineChanged: boolean): void {
    const met = this.currentMet();
    // Frame and status messages also notify the state; the MET rarely moves
    if (!timelineChanged && met === this.lastMet) return;
    this.lastMet = met;

    const events = this.timeline.events;
    const current = currentEventIndex(events, met);
    const next = nextEventIndex(events, met);

    this.empty.hidden = events.length > 0;
    this.list.hidden = events.length === 0;
    // A countdown hold shows next to the clock while that clock is the one shown
    const snapshot = this.timeline.statuses.snapshot;
    const holding = snapshot?.clock_source === 'countdown' && snapshot.hold === true && met === snapshot.met;
    this.clock.textContent = holding ? `${formatClock(met)} HOLD` : formatClock(met);

    this.syncRows(this.visibleEvents(events, current, next));
    this.updateRows(events, met, current, next);
    this.updateSummary(events, met, next);
    this.autoScroll();
  }

  /** The events with rows: all of them, or the current one and the next in the ticker. */
  private visibleEvents(events: readonly TimelineEvent[], current: number | null, next: number | null): readonly TimelineEvent[] {
    if (!this.compact) return events;
    const shown = [current, next].filter((i): i is number => i !== null).map((i) => events[i]);
    return shown.length === 0 && events.length > 0 ? [events[0]] : shown;
  }

  /** Builds the rows when the events to show changed. */
  private syncRows(visible: readonly TimelineEvent[]): void {
    const key = visible.map((e) => `${e.id}|${e.t}|${e.event}|${e.vehicle}`).join('\n');
    if (key === this.builtFor) return;
    this.builtFor = key;
    this.rows = new Map();
    this.currentId = null;
    this.list.replaceChildren(...visible.map((event) => this.buildRow(event)));
  }

  private buildRow(event: TimelineEvent): HTMLLIElement {
    const element = document.createElement('li');
    element.className = 'timeline-row';
    element.dataset.eventId = event.id;

    const circle = document.createElement(this.chrome ? 'button' : 'span');
    circle.className = 'timeline-circle';
    if (circle instanceof HTMLButtonElement) {
      circle.type = 'button';
      circle.setAttribute('aria-haspopup', 'menu');
      circle.setAttribute('aria-expanded', 'false');
      circle.addEventListener('click', () => this.openMenu(event, circle));
    }

    const time = document.createElement('span');
    time.className = 'timeline-row__time';
    time.textContent = formatTimelineTime(event.t);

    const text = document.createElement('span');
    text.className = 'timeline-row__text';
    text.textContent = event.event;
    text.title = event.event;

    const tag = document.createElement('span');
    tag.className = `timeline-row__vehicle timeline-row__vehicle--${event.vehicle}`;
    tag.textContent = VEHICLE_TAGS[event.vehicle];
    tag.title = { superheavy: 'Super Heavy', starship: 'Starship', both: 'Both vehicles' }[event.vehicle];

    const eta = document.createElement('span');
    eta.className = 'timeline-row__eta';

    element.append(circle, time, text, tag, eta);
    this.rows.set(event.id, { element, circle, eta, shown: '' });
    return element;
  }

  private updateRows(events: readonly TimelineEvent[], met: number | null, current: number | null, next: number | null): void {
    const byId = new Map(events.map((e) => [e.id, e]));
    for (const [id, row] of this.rows) {
      const event = byId.get(id);
      if (!event) continue;
      const status = this.timeline.statuses.status(id) ?? NO_STATUS;
      const shown = `${status.status}|${status.source}|${status.evidence}|${status.met}`;
      if (shown !== row.shown) {
        row.shown = shown;
        fillStatusCircle(row.circle, status.status, status.source, statusTooltip(event, status), event.event);
        row.element.dataset.status = status.status;
      }
    }

    const currentId = current === null ? null : events[current].id;
    if (currentId !== this.currentId) {
      if (this.currentId) this.rows.get(this.currentId)?.element.classList.remove('timeline-row--current');
      this.currentId = currentId;
    }
    if (currentId) {
      const element = this.rows.get(currentId)?.element;
      element?.classList.add('timeline-row--current');
      element?.setAttribute('aria-current', 'step');
    }
    for (const [id, row] of this.rows) {
      if (id !== currentId) row.element.removeAttribute('aria-current');
      const isNext = next !== null && events[next].id === id;
      row.eta.textContent = this.compact && isNext && met !== null ? `in ${formatDuration(events[next].t - met)}` : '';
    }
  }

  private updateSummary(events: readonly TimelineEvent[], met: number | null, next: number | null): void {
    const pending = this.timeline.statuses.pending;
    this.pending.textContent = pendingText(pending);
    this.pending.hidden = pending <= 0;

    if (events.length === 0) this.next.textContent = '';
    else if (met === null) this.next.textContent = 'Waiting for the clock';
    else if (next === null) this.next.textContent = 'No more events';
    else this.next.textContent = `next: ${events[next].event} in ${formatDuration(events[next].t - met)}`;
  }

  /** Keeps the current event in view, unless the user scrolled the list lately. */
  private autoScroll(): void {
    if (this.compact || !this.currentId) return;
    if (Date.now() - this.lastUserScrollAt < AUTO_SCROLL_PAUSE_MS) return;
    const row = this.rows.get(this.currentId)?.element;
    if (!row) return;
    const top = row.offsetTop;
    const bottom = top + row.offsetHeight;
    const viewTop = this.list.scrollTop;
    const viewBottom = viewTop + this.list.clientHeight;
    if (top >= viewTop && bottom <= viewBottom) return;
    this.list.scrollTop = Math.max(0, top - (this.list.clientHeight - row.offsetHeight) / 2);
  }

  private openMenu(event: TimelineEvent, anchor: HTMLElement): void {
    const status = this.timeline.statuses.status(event.id) ?? NO_STATUS;
    toggleOverrideMenu({
      anchor,
      eventText: event.event,
      status: status.status,
      source: status.source,
      onChoose: (chosen) => void this.setStatus(event, chosen),
    });
  }

  /** Sends a person's status; the snapshot that follows redraws the circle. */
  private async setStatus(event: TimelineEvent, status: TimelineStatusValue | null): Promise<void> {
    const row = this.rows.get(event.id);
    this.showError(null);
    row?.element.classList.add('timeline-row--saving');
    row?.circle.setAttribute('aria-busy', 'true');
    try {
      const payload = await this.override(event.id, status);
      // The WebSocket's snapshot usually got here first; an older one is ignored
      this.timeline.statuses.apply(payload);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      logger.warn('Status not set', { event: event.id, error: message });
      this.showError(
        error instanceof TypeError ? `Could not set "${event.event}": backend unreachable` : `Could not set "${event.event}": ${message}`,
      );
    } finally {
      row?.element.classList.remove('timeline-row--saving');
      row?.circle.removeAttribute('aria-busy');
    }
  }

  private showError(message: string | null): void {
    this.errorLine.textContent = message ?? '';
    this.errorLine.hidden = message === null;
  }
}
