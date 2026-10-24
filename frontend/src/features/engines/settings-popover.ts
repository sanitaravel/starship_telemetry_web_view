import type { CurrentMission } from '../../state/current-mission';
import type { Vehicle } from '../../domain/engines/layout';
import { MAX_SERIAL_LENGTH, normaliseSerial, type EngineSerials } from '../../state/engine-serials';
import { MissionFields } from './mission-fields';
import { toggleTimelineEditor } from './timeline-editor';
import { anchorPanel, bindDismiss, type AnchoredPanel } from '../../ui/popover';
import { svgEl } from '../../ui/svg';

/** Raptor engine serials all start with this letter (e.g. "R576"). */
export const SERIAL_PREFIX = 'R';

/**
 * The full serial for what was typed after the fixed prefix. A typed prefix
 * is dropped rather than doubled, so "576" and "R576" both give "R576";
 * nothing typed gives "" (no serial).
 */
export function withPrefix(typed: string): string {
  const rest = normaliseSerial(typed).replace(new RegExp(`^${SERIAL_PREFIX}+`), '');
  return rest ? SERIAL_PREFIX + rest : '';
}

/** What the field shows for a stored serial: the part after the prefix. */
export function withoutPrefix(serial: string | undefined): string {
  if (!serial) return '';
  return serial.startsWith(SERIAL_PREFIX) ? serial.slice(SERIAL_PREFIX.length) : serial;
}

/** One engine row in the settings panel. */
export interface EngineField {
  id: string;
  /** What the diagram shows when no serial is set, e.g. "E6". */
  label: string;
}

/** A titled group of engine rows, e.g. the sea-level engines. */
export interface EngineGroup {
  title: string;
  engines: EngineField[];
}

/** What one vehicle's settings panel shows. */
export interface EngineSettingsOptions {
  title: string;
  groups: EngineGroup[];
  serials: EngineSerials;
  /** The mission to name and save to; without one only the serials are shown. */
  mission?: CurrentMission;
  /** Whose name the panel asks for next to the mission: the ship's or the booster's. */
  vehicle?: Vehicle;
  /** A wider panel with the engines three to a line, for Super Heavy's 33. */
  wide?: boolean;
}

/** Gear icon (24×24 outline), drawn in the button's text colour. */
const GEAR_PATH =
  'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z' +
  'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 ' +
  '1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06' +
  'a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09' +
  'A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68' +
  'a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06' +
  'a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09' +
  'a1.65 1.65 0 0 0-1.51 1Z';

/** Gear button that opens a vehicle's engine settings. */
export function createSettingsButton(vehicleName: string): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'engine-settings-btn';
  button.title = `${vehicleName} engine settings`;
  button.setAttribute('aria-label', `${vehicleName} engine settings`);
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');

  const svg = svgEl('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' });
  const path = svgEl('path', {
    d: GEAR_PATH,
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': '1.8',
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
  });
  svg.appendChild(path);
  button.appendChild(svg);
  return button;
}

/**
 * Panel where the mission's name, ship and engine serials are typed in. Each
 * change is saved as it is typed, so closing the panel never loses anything.
 */
class EngineSettingsPopover {
  private readonly panel: HTMLElement;
  private readonly heading: HTMLElement;
  private readonly missionSlot: HTMLElement;
  private readonly body: HTMLElement;
  private readonly statusSlot: HTMLElement;
  private readonly clearButton: HTMLButtonElement;
  private readonly timelineButton: HTMLButtonElement;
  private readonly anchored: AnchoredPanel;
  private missionFields: MissionFields | null = null;
  private inputs: HTMLInputElement[] = [];
  private anchor: HTMLElement | null = null;
  private serials: EngineSerials | null = null;
  private unsubscribe: (() => void) | null = null;
  private mission: CurrentMission | null = null;

  constructor() {
    this.panel = document.createElement('div');
    this.panel.className = 'engine-settings';
    this.panel.setAttribute('role', 'dialog');
    this.panel.hidden = true;

    this.heading = document.createElement('p');
    this.heading.className = 'engine-settings__title';

    const hint = document.createElement('p');
    hint.className = 'engine-settings__hint';
    hint.textContent = 'Raptor serials for this mission, shown when hovering an engine.';

    this.missionSlot = document.createElement('div');
    // Opens the mission's timeline editor in place of this panel
    this.timelineButton = document.createElement('button');
    this.timelineButton.type = 'button';
    this.timelineButton.className = 'engine-settings__button engine-settings__timeline';
    this.timelineButton.setAttribute('aria-haspopup', 'dialog');
    this.timelineButton.addEventListener('click', () => {
      const { anchor, mission } = this;
      if (!anchor || !mission) return;
      this.close(false);
      toggleTimelineEditor(anchor, mission);
    });
    this.body = document.createElement('div');
    this.body.className = 'engine-settings__groups';
    this.statusSlot = document.createElement('div');

    const actions = document.createElement('div');
    actions.className = 'engine-settings__actions';
    this.clearButton = document.createElement('button');
    this.clearButton.type = 'button';
    this.clearButton.className = 'engine-settings__button';
    this.clearButton.textContent = 'Clear all';
    this.clearButton.addEventListener('click', () => {
      this.serials?.clear();
      this.inputs[0]?.focus();
    });
    const doneButton = document.createElement('button');
    doneButton.type = 'button';
    doneButton.className = 'engine-settings__button engine-settings__button--primary';
    doneButton.textContent = 'Done';
    doneButton.addEventListener('click', () => this.close());
    actions.append(this.clearButton, doneButton);

    this.panel.append(this.heading, hint, this.missionSlot, this.body, this.statusSlot, actions);
    document.body.appendChild(this.panel);

    bindDismiss(this.panel, {
      onDismiss: (reason) => this.close(reason === 'escape'),
      ignore: () => [this.anchor],
    });
    this.panel.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target instanceof HTMLInputElement) {
        e.preventDefault();
        this.close();
      }
    });
    // Below the gear button, lined up with its right edge
    this.anchored = anchorPanel(this.panel, () => this.anchor, {
      align: 'right',
      onAnchorRemoved: () => this.close(false),
    });
  }

  toggle(anchor: HTMLElement, options: EngineSettingsOptions): void {
    const { title, groups, serials, mission, vehicle = 'starship', wide = false } = options;
    if (!this.panel.hidden && this.anchor === anchor) {
      this.close();
      return;
    }
    this.close(false);
    this.anchor = anchor;
    this.serials = serials;
    this.heading.textContent = title;
    this.panel.setAttribute('aria-label', title);
    this.panel.classList.toggle('engine-settings--wide', wide);
    this.build(groups, serials);
    if (mission) {
      this.missionFields = new MissionFields(mission, vehicle);
      this.mission = mission;
      const count = mission.timeline.length;
      this.timelineButton.textContent = count > 0 ? `Timeline (${count})` : 'Timeline';
      this.missionSlot.replaceChildren(this.missionFields.element, this.timelineButton);
      this.statusSlot.replaceChildren(this.missionFields.statusLine);
    }
    this.unsubscribe = serials.subscribe(() => this.refresh());
    this.refresh();
    this.panel.hidden = false;
    anchor.setAttribute('aria-expanded', 'true');
    this.anchored.reposition();
    (this.missionFields?.missionInput ?? this.inputs[0])?.focus();
  }

  /** Closes the panel, returning focus to its button unless the user clicked elsewhere. */
  close(restoreFocus = true): void {
    if (this.panel.hidden) return;
    this.panel.hidden = true;
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.missionFields?.dispose();
    this.missionFields = null;
    this.missionSlot.replaceChildren();
    this.statusSlot.replaceChildren();
    this.anchor?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) this.anchor?.focus();
    this.anchor = null;
    this.serials = null;
    this.mission = null;
  }

  private build(groups: EngineGroup[], serials: EngineSerials): void {
    this.body.replaceChildren();
    this.inputs = [];
    for (const group of groups) {
      const fieldset = document.createElement('fieldset');
      fieldset.className = 'engine-settings__group';
      const legend = document.createElement('legend');
      legend.className = 'engine-settings__group-title';
      legend.textContent = group.title;
      const rows = document.createElement('div');
      rows.className = 'engine-settings__rows';
      fieldset.append(legend, rows);

      for (const engine of group.engines) {
        const row = document.createElement('label');
        row.className = 'engine-settings__row';
        const name = document.createElement('span');
        name.className = 'engine-settings__engine';
        name.textContent = engine.label;
        // The "R" every Raptor serial starts with is fixed in front of the
        // field, so only the rest is typed
        const field = document.createElement('span');
        field.className = 'engine-settings__field';
        const prefix = document.createElement('span');
        prefix.className = 'engine-settings__prefix';
        prefix.textContent = SERIAL_PREFIX;
        prefix.setAttribute('aria-hidden', 'true');
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'engine-settings__input';
        input.dataset.engine = engine.id;
        input.maxLength = MAX_SERIAL_LENGTH - SERIAL_PREFIX.length;
        input.placeholder = '576';
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.setAttribute('aria-label', `${engine.label} serial number, after the ${SERIAL_PREFIX}`);
        input.addEventListener('input', () => serials.set(engine.id, withPrefix(input.value)));
        // Show the stored form (upper case, stray characters dropped)
        input.addEventListener('change', () => (input.value = withoutPrefix(serials.get(engine.id))));
        field.append(prefix, input);
        row.append(name, field);
        rows.appendChild(row);
        this.inputs.push(input);
      }
      this.body.appendChild(fieldset);
    }
  }

  /** Mirrors the stored serials into the fields, except the one being typed in. */
  private refresh(): void {
    if (!this.serials) return;
    for (const input of this.inputs) {
      if (input === document.activeElement) continue;
      input.value = withoutPrefix(this.serials.get(input.dataset.engine!));
    }
    this.clearButton.disabled = this.serials.isEmpty;
  }
}

let popover: EngineSettingsPopover | null = null;

/**
 * Opens the engine settings for a vehicle under `anchor`, or closes them if
 * already open there. With a `mission`, the panel also names the mission and
 * the vehicle (ship or booster) and saves everything to the mission's file.
 */
export function toggleEngineSettings(anchor: HTMLElement, options: EngineSettingsOptions): void {
  popover ??= new EngineSettingsPopover();
  popover.toggle(anchor, options);
}
