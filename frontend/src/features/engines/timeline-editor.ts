import type { CurrentMission } from '../../state/current-mission';
import { anchorPanel, bindDismiss, type AnchoredPanel } from '../../ui/popover';
import { describeStatus } from './mission-fields';
import { buildTimeline, formatTimelineTime, parseTimelinePaste, type TimelineSection, type TimelineWarning } from '../../domain/timeline/parse';
import {
  MAX_EVENT_TEXT,
  MAX_HUD_LABEL,
  MAX_TIMELINE_EVENTS,
  TIMELINE_AUTO_KINDS,
  TIMELINE_RULES,
  TIMELINE_RULE_LABELS,
  TIMELINE_VEHICLES,
  type TimelineAuto,
  type TimelineAutoKind,
  type TimelineEvent,
  type TimelineRule,
  type TimelineVehicle,
} from '../../domain/timeline/types';

const VEHICLE_LABELS: Record<TimelineVehicle, string> = {
  starship: 'Starship',
  superheavy: 'Super Heavy',
  both: 'Both',
};

const AUTO_LABELS: Record<TimelineAutoKind, string> = {
  manual: 'Manual',
  clock: 'Clock',
  hud: 'HUD label',
  rule: 'Telemetry rule',
};

const SECTIONS: { section: TimelineSection; label: string; placeholder: string }[] = [
  { section: 'countdown', label: 'Countdown', placeholder: 'Hr/Min/Sec\tEvent\n00:50:00\tSpaceX Flight Director conducts poll…' },
  { section: 'flight', label: 'Flight test', placeholder: 'Hr/Min/Sec\tEvent\n00:00:00\tLiftoff\n00:00:58\tMax Q…' },
];

/** A warning with the box it came from, for the list under the boxes. */
interface BoxWarning extends TimelineWarning {
  section: TimelineSection;
}

/** The first problem that keeps the timeline from being saved, or null. */
export function timelineProblem(events: readonly TimelineEvent[]): string | null {
  if (events.length > MAX_TIMELINE_EVENTS) return `At most ${MAX_TIMELINE_EVENTS} events (${events.length} here)`;
  for (const event of events) {
    const label = formatTimelineTime(event.t);
    const text = event.event.trim();
    if (text === '') return `${label}: the event needs a text`;
    if (text.length > MAX_EVENT_TEXT) return `${label}: the text is longer than ${MAX_EVENT_TEXT} characters`;
    if (event.auto.kind === 'hud' && event.auto.label.trim() === '') return `${label}: the HUD label is empty`;
  }
  return null;
}

/**
 * Popover where a mission's timeline is entered: the two tables on SpaceX's
 * mission page are pasted into the Countdown and Flight test boxes, parsed as
 * they are typed, and shown as a table whose event text, vehicle and
 * automation source can be changed before Save. Without pasted text the
 * table shows the mission's saved timeline. Needs a named mission, like the
 * serials, since the timeline is saved in the mission's file.
 */
class TimelineEditorPopover {
  private readonly panel: HTMLElement;
  private readonly boxes = new Map<TimelineSection, HTMLTextAreaElement>();
  private readonly warningList: HTMLUListElement;
  private readonly summary: HTMLElement;
  private readonly tableBody: HTMLTableSectionElement;
  private readonly notice: HTMLElement;
  private readonly statusLine: HTMLElement;
  private readonly clearButton: HTMLButtonElement;
  private readonly saveButton: HTMLButtonElement;
  private readonly anchored: AnchoredPanel;
  private anchor: HTMLElement | null = null;
  private mission: CurrentMission | null = null;
  private unsubscribe: (() => void) | null = null;
  /** What the table shows and Save writes. */
  private rows: TimelineEvent[] = [];

  constructor() {
    this.panel = document.createElement('div');
    this.panel.className = 'timeline-editor';
    this.panel.setAttribute('role', 'dialog');
    this.panel.setAttribute('aria-label', 'Mission timeline');
    this.panel.hidden = true;

    const heading = document.createElement('p');
    heading.className = 'engine-settings__title';
    heading.textContent = 'Mission timeline';
    const hint = document.createElement('p');
    hint.className = 'engine-settings__hint';
    hint.textContent =
      'Paste the Countdown and Flight test tables from the mission page. Rows are read as you paste; ' +
      'the box decides whether a time is before or after T-0 unless the row has its own T- or T+.';

    const boxes = document.createElement('div');
    boxes.className = 'timeline-editor__boxes';
    for (const { section, label, placeholder } of SECTIONS) {
      const wrap = document.createElement('label');
      wrap.className = 'timeline-editor__box';
      const caption = document.createElement('span');
      caption.className = 'engine-settings__mission-label';
      caption.textContent = label;
      const area = document.createElement('textarea');
      area.className = 'engine-settings__text timeline-editor__area';
      area.dataset.section = section;
      area.id = `timeline-editor-${section}`;
      area.name = `timeline-${section}`;
      area.rows = 5;
      area.spellcheck = false;
      area.placeholder = placeholder;
      area.setAttribute('aria-label', `${label} timeline text`);
      area.addEventListener('input', () => this.reparse());
      wrap.append(caption, area);
      boxes.appendChild(wrap);
      this.boxes.set(section, area);
    }

    this.warningList = document.createElement('ul');
    this.warningList.className = 'timeline-editor__warnings';
    this.warningList.setAttribute('aria-label', 'Lines that could not be read');

    this.summary = document.createElement('p');
    this.summary.className = 'engine-settings__hint';

    const table = document.createElement('table');
    table.className = 'timeline-editor__table';
    const head = table.createTHead().insertRow();
    for (const title of ['Time', 'Event', 'Vehicle', 'Source', '']) {
      const cell = document.createElement('th');
      cell.textContent = title;
      head.appendChild(cell);
    }
    this.tableBody = table.createTBody();
    const scroll = document.createElement('div');
    scroll.className = 'timeline-editor__scroll';
    scroll.appendChild(table);

    this.notice = document.createElement('p');
    this.notice.className = 'engine-settings__status';
    this.notice.setAttribute('role', 'alert');
    this.statusLine = document.createElement('p');
    this.statusLine.className = 'engine-settings__status';
    this.statusLine.setAttribute('role', 'status');

    const actions = document.createElement('div');
    actions.className = 'engine-settings__actions';
    this.clearButton = this.button('Clear timeline', () => {
      for (const area of this.boxes.values()) area.value = '';
      this.warningList.replaceChildren();
      this.rows = [];
      this.renderTable();
    });
    const cancel = this.button('Cancel', () => this.close());
    this.saveButton = this.button('Save', () => this.save(), true);
    const right = document.createElement('div');
    right.className = 'timeline-editor__buttons';
    right.append(cancel, this.saveButton);
    actions.append(this.clearButton, right);

    this.panel.append(heading, hint, boxes, this.warningList, this.summary, scroll, this.notice, this.statusLine, actions);
    document.body.appendChild(this.panel);

    bindDismiss(this.panel, { onDismiss: (reason) => this.close(reason === 'escape') });
    this.anchored = anchorPanel(this.panel, () => this.anchor, {
      align: 'right',
      onAnchorRemoved: () => this.close(false),
    });
    // Pasting grows the preview and the text boxes can be resized: keep the panel on screen
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => {
        if (!this.panel.hidden) this.anchored.reposition();
      }).observe(this.panel);
    }
  }

  toggle(anchor: HTMLElement, mission: CurrentMission): void {
    if (!this.panel.hidden && this.anchor === anchor) {
      this.close();
      return;
    }
    this.close(false);
    this.anchor = anchor;
    this.mission = mission;
    for (const area of this.boxes.values()) area.value = '';
    this.warningList.replaceChildren();
    this.rows = structuredClone([...mission.timeline]);
    this.renderTable();
    this.unsubscribe = mission.subscribe(() => this.refreshStatus());
    this.refreshStatus();
    this.panel.hidden = false;
    this.anchored.reposition();
    this.boxes.get('countdown')?.focus();
  }

  close(restoreFocus = true): void {
    if (this.panel.hidden) return;
    this.panel.hidden = true;
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.tableBody.replaceChildren();
    if (restoreFocus) this.anchor?.focus();
    this.anchor = null;
    this.mission = null;
  }

  private button(label: string, onClick: () => void, primary = false): HTMLButtonElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'engine-settings__button' + (primary ? ' engine-settings__button--primary' : '');
    button.textContent = label;
    button.addEventListener('click', onClick);
    return button;
  }

  private get named(): boolean {
    return this.mission?.status.kind !== 'unnamed';
  }

  /** Re-reads both boxes. With text in either, the table shows what was read; with both empty, it keeps what it had. */
  private reparse(): void {
    const drafts = [];
    const warnings: BoxWarning[] = [];
    let pasted = false;
    for (const { section } of SECTIONS) {
      const text = this.boxes.get(section)!.value;
      if (text.trim() !== '') pasted = true;
      const result = parseTimelinePaste(text, section);
      drafts.push(...result.events);
      warnings.push(...result.warnings.map((w) => ({ ...w, section })));
    }
    this.warningList.replaceChildren(
      ...warnings.map((w) => {
        const item = document.createElement('li');
        item.textContent = `${SECTIONS.find((s) => s.section === w.section)!.label}, line ${w.line}: ${w.reason} — "${w.text}"`;
        return item;
      })
    );
    if (pasted) {
      // Rows already in the saved timeline keep their ids, and with them their statuses
      this.rows = buildTimeline(drafts, this.mission?.timeline ?? []);
      this.renderTable();
    }
  }

  private renderTable(): void {
    this.tableBody.replaceChildren(...this.rows.map((row) => this.renderRow(row)));
    this.summary.textContent = this.rows.length === 0 ? 'No events yet.' : `${this.rows.length} events`;
    this.updateState();
  }

  private renderRow(row: TimelineEvent): HTMLTableRowElement {
    const tr = document.createElement('tr');
    tr.dataset.id = row.id;

    const time = tr.insertCell();
    time.className = 'timeline-editor__time';
    time.textContent = formatTimelineTime(row.t);

    const text = document.createElement('input');
    text.type = 'text';
    text.name = `event-${row.id}`;
    text.className = 'engine-settings__text';
    text.value = row.event;
    text.setAttribute('aria-label', `Event at ${formatTimelineTime(row.t)}`);
    text.addEventListener('input', () => {
      row.event = text.value;
      this.updateState();
    });
    tr.insertCell().appendChild(text);

    const vehicle = this.select(TIMELINE_VEHICLES, VEHICLE_LABELS, row.vehicle, `Vehicle at ${formatTimelineTime(row.t)}`, `vehicle-${row.id}`);
    vehicle.addEventListener('change', () => (row.vehicle = vehicle.value as TimelineVehicle));
    tr.insertCell().appendChild(vehicle);

    const source = tr.insertCell();
    source.className = 'timeline-editor__source';
    const renderSource = () => {
      const kind = this.select(TIMELINE_AUTO_KINDS, AUTO_LABELS, row.auto.kind, `Source at ${formatTimelineTime(row.t)}`, `source-${row.id}`);
      kind.addEventListener('change', () => {
        row.auto = defaultAuto(kind.value as TimelineAutoKind, row.event);
        renderSource();
        this.updateState();
      });
      source.replaceChildren(kind);
      const detail = this.autoDetail(row);
      if (detail) source.appendChild(detail);
    };
    renderSource();

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'engine-settings__button timeline-editor__remove';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `Remove event at ${formatTimelineTime(row.t)}`);
    remove.addEventListener('click', () => {
      this.rows = this.rows.filter((r) => r !== row);
      this.renderTable();
    });
    tr.insertCell().appendChild(remove);
    return tr;
  }

  /** The HUD label field or rule select that goes with a source, if it has one. */
  private autoDetail(row: TimelineEvent): HTMLElement | null {
    const auto = row.auto;
    if (auto.kind === 'hud') {
      const label = document.createElement('input');
      label.type = 'text';
      label.name = `hud-label-${row.id}`;
      label.className = 'engine-settings__text';
      label.maxLength = MAX_HUD_LABEL;
      label.value = auto.label;
      label.placeholder = 'MAX Q';
      label.setAttribute('aria-label', `HUD label at ${formatTimelineTime(row.t)}`);
      label.addEventListener('input', () => {
        row.auto = { kind: 'hud', label: label.value };
        this.updateState();
      });
      return label;
    }
    if (auto.kind === 'rule') {
      const labels = TIMELINE_RULE_LABELS;
      const rule = this.select(TIMELINE_RULES, labels, auto.rule, `Rule at ${formatTimelineTime(row.t)}`, `rule-${row.id}`);
      rule.addEventListener('change', () => (row.auto = { kind: 'rule', rule: rule.value as TimelineRule }));
      return rule;
    }
    return null;
  }

  private select<T extends string>(values: readonly T[], labels: Record<T, string>, current: T, label: string, name: string): HTMLSelectElement {
    const select = document.createElement('select');
    select.name = name;
    select.className = 'engine-settings__text';
    select.setAttribute('aria-label', label);
    for (const value of values) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = labels[value];
      select.appendChild(option);
    }
    select.value = current;
    return select;
  }

  /** Save is possible with a named mission and a valid table; says why not otherwise. */
  private updateState(): void {
    const problem = this.named ? timelineProblem(this.rows) : 'Name the mission first: the timeline is saved in its file';
    this.notice.textContent = problem ?? '';
    this.saveButton.disabled = problem !== null;
    this.clearButton.disabled = this.rows.length === 0 && [...this.boxes.values()].every((a) => a.value === '');
  }

  private refreshStatus(): void {
    if (!this.mission) return;
    this.statusLine.textContent = describeStatus(this.mission.status);
    this.updateState();
  }

  private save(): void {
    if (!this.mission || this.saveButton.disabled) return;
    this.mission.setTimeline(this.rows.map((row) => ({ ...row, event: row.event.trim() })));
    this.close();
  }
}

/** The source a row switches to when its kind is changed: a HUD label starts as a guess from the text. */
function defaultAuto(kind: TimelineAutoKind, eventText: string): TimelineAuto {
  switch (kind) {
    case 'hud':
      return { kind, label: eventText.replace(/\(.*$/, '').trim().toUpperCase().slice(0, MAX_HUD_LABEL) };
    case 'rule':
      return { kind, rule: TIMELINE_RULES[0] };
    default:
      return { kind };
  }
}

let popover: TimelineEditorPopover | null = null;

/** Opens the timeline editor under `anchor`, or closes it if already open there. */
export function toggleTimelineEditor(anchor: HTMLElement, mission: CurrentMission): void {
  popover ??= new TimelineEditorPopover();
  popover.toggle(anchor, mission);
}
