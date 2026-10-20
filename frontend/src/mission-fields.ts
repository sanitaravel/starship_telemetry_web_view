import type { CurrentMission, SaveStatus, Vehicle } from './current-mission';
import { missionId, type MissionChoice } from './missions-api';

/** Most suggestions listed under the mission field at once. */
const MAX_SUGGESTIONS = 8;

/** The name field each vehicle's panel shows under the mission. */
const NAME_FIELDS: Record<Vehicle, { field: string; label: string; placeholder: string }> = {
  starship: { field: 'ship', label: 'Ship', placeholder: 'S35' },
  superheavy: { field: 'booster', label: 'Booster', placeholder: 'B16' },
};

/** What a suggestion shows next to the mission name, e.g. "S41 · B15". */
export function describeChoice(choice: MissionChoice): string {
  if (!choice.saved) return 'recorded flight';
  return [choice.ship, choice.booster].filter(Boolean).join(' · ') || 'saved';
}

let nextListId = 0;

/** True when `choice` matches what was typed (anywhere in its name or id). */
export function matchesQuery(choice: MissionChoice, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const qId = missionId(q);
  return choice.mission.toLowerCase().includes(q) || (qId !== '' && choice.id.includes(qId));
}

/** The status line's text for a save status. */
export function describeStatus(status: SaveStatus): string {
  switch (status.kind) {
    case 'unnamed':
      return 'Name the mission to save it to a file';
    case 'idle':
      return `File: ${status.file}`;
    case 'saving':
      return 'Saving…';
    case 'saved':
      return `Saved to ${status.file}`;
    case 'error':
      return status.message;
  }
}

/**
 * Mission field and the vehicle's name field (ship or booster) of an engine
 * settings panel. The mission is the same in both vehicles' panels. The mission field
 * suggests saved missions and recorded flights matching what is typed (or,
 * on a click or arrow down, all of them); picking one loads it. A name is committed on Enter, on picking a
 * suggestion or on leaving the field, never on each keystroke, so typing
 * "IFT 1" on the way to "IFT 14" cannot load IFT 1 by accident.
 */
export class MissionFields {
  readonly element: HTMLElement;
  readonly statusLine: HTMLElement;
  readonly missionInput: HTMLInputElement;
  /** The ship's name in Starship's panel, the booster's in Super Heavy's. */
  readonly nameInput: HTMLInputElement;
  private readonly list: HTMLUListElement;
  private choices: MissionChoice[] = [];
  private shown: MissionChoice[] = [];
  private highlighted = -1;
  /** Last name sent to the mission, so Enter followed by blur commits once. */
  private committed: string;
  private readonly unsubscribe: () => void;

  constructor(
    private readonly mission: CurrentMission,
    private readonly vehicle: Vehicle = 'starship'
  ) {
    const listId = `mission-suggestions-${nextListId++}`;

    this.element = document.createElement('fieldset');
    this.element.className = 'engine-settings__mission';

    const missionRow = document.createElement('label');
    missionRow.className = 'engine-settings__mission-row';
    const missionLabel = document.createElement('span');
    missionLabel.className = 'engine-settings__mission-label';
    missionLabel.textContent = 'Mission';
    const combo = document.createElement('span');
    combo.className = 'engine-settings__combo';
    this.missionInput = document.createElement('input');
    this.missionInput.type = 'text';
    this.missionInput.className = 'engine-settings__text';
    this.missionInput.dataset.field = 'mission';
    this.missionInput.placeholder = 'IFT 14';
    this.missionInput.maxLength = 80;
    this.missionInput.autocomplete = 'off';
    this.missionInput.spellcheck = false;
    this.missionInput.setAttribute('aria-label', 'Mission name');
    this.missionInput.setAttribute('role', 'combobox');
    this.missionInput.setAttribute('aria-autocomplete', 'list');
    this.missionInput.setAttribute('aria-expanded', 'false');
    this.missionInput.setAttribute('aria-controls', listId);
    this.list = document.createElement('ul');
    this.list.id = listId;
    this.list.className = 'engine-settings__suggestions';
    this.list.setAttribute('role', 'listbox');
    this.list.setAttribute('aria-label', 'Saved missions');
    this.list.hidden = true;
    combo.append(this.missionInput, this.list);
    missionRow.append(missionLabel, combo);

    const names = NAME_FIELDS[vehicle];
    const nameRow = document.createElement('label');
    nameRow.className = 'engine-settings__mission-row';
    const nameLabel = document.createElement('span');
    nameLabel.className = 'engine-settings__mission-label';
    nameLabel.textContent = names.label;
    this.nameInput = document.createElement('input');
    this.nameInput.type = 'text';
    this.nameInput.className = 'engine-settings__text';
    this.nameInput.dataset.field = names.field;
    this.nameInput.placeholder = names.placeholder;
    this.nameInput.maxLength = 40;
    this.nameInput.autocomplete = 'off';
    this.nameInput.spellcheck = false;
    this.nameInput.setAttribute('aria-label', `${names.label} name`);
    nameRow.append(nameLabel, this.nameInput);

    this.element.append(missionRow, nameRow);

    this.statusLine = document.createElement('p');
    this.statusLine.className = 'engine-settings__status';
    this.statusLine.setAttribute('role', 'status');

    // The list opens when asked for (typing, a click on the field, arrow
    // down), not just because the panel opened with the focus here. Focus
    // re-reads it, so a mission created a moment ago is offered.
    this.missionInput.addEventListener('focus', () => void this.loadChoices());
    this.missionInput.addEventListener('click', () => this.showSuggestions());
    this.missionInput.addEventListener('input', () => this.showSuggestions());
    this.missionInput.addEventListener('keydown', (e) => this.onMissionKey(e));
    this.missionInput.addEventListener('change', () => this.commitTyped());
    this.missionInput.addEventListener('blur', () => this.hideSuggestions());
    this.nameInput.addEventListener('change', () =>
      this.mission.setVehicleName(this.vehicle, this.nameInput.value)
    );

    this.committed = mission.mission;
    this.unsubscribe = mission.subscribe(() => this.refresh());
    this.refresh();
  }

  /** Stops following the mission; call when the panel closes. */
  dispose(): void {
    this.unsubscribe();
    this.hideSuggestions();
  }

  private async loadChoices(): Promise<void> {
    try {
      this.choices = await this.mission.listChoices();
    } catch {
      this.choices = [];
    }
    // Refresh an open list; never open it from here
    if (!this.list.hidden) this.showSuggestions();
  }

  /** Mirrors the current mission into the fields, except one being typed in. */
  private refresh(): void {
    if (document.activeElement !== this.missionInput) this.missionInput.value = this.mission.mission;
    if (document.activeElement !== this.nameInput) {
      this.nameInput.value = this.mission.vehicleName(this.vehicle);
    }
    const { status } = this.mission;
    this.statusLine.textContent = describeStatus(status);
    this.statusLine.dataset.kind = status.kind;
  }

  private showSuggestions(): void {
    this.shown = this.choices
      .filter((choice) => matchesQuery(choice, this.missionInput.value))
      .slice(0, MAX_SUGGESTIONS);
    this.highlighted = -1;
    this.list.replaceChildren(
      ...this.shown.map((choice, index) => {
        const item = document.createElement('li');
        item.id = `${this.list.id}-${index}`;
        item.className = 'engine-settings__suggestion';
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', 'false');
        item.dataset.mission = choice.id;
        const name = document.createElement('span');
        name.textContent = choice.mission;
        const detail = document.createElement('span');
        detail.className = 'engine-settings__suggestion-detail';
        detail.textContent = describeChoice(choice);
        item.append(name, detail);
        // Keep focus in the field, or its blur would close the list first
        item.addEventListener('pointerdown', (e) => e.preventDefault());
        item.addEventListener('click', () => this.pick(choice));
        return item;
      })
    );
    const open = this.shown.length > 0;
    this.list.hidden = !open;
    this.missionInput.setAttribute('aria-expanded', String(open));
    this.missionInput.removeAttribute('aria-activedescendant');
  }

  private hideSuggestions(): void {
    this.list.hidden = true;
    this.highlighted = -1;
    this.missionInput.setAttribute('aria-expanded', 'false');
    this.missionInput.removeAttribute('aria-activedescendant');
  }

  private highlight(index: number): void {
    const items = [...this.list.children];
    this.highlighted = index;
    items.forEach((item, i) => item.setAttribute('aria-selected', String(i === index)));
    if (index >= 0) this.missionInput.setAttribute('aria-activedescendant', items[index].id);
    else this.missionInput.removeAttribute('aria-activedescendant');
  }

  private onMissionKey(e: KeyboardEvent): void {
    const open = !this.list.hidden;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) this.showSuggestions();
      if (this.shown.length === 0) return;
      const step = e.key === 'ArrowDown' ? 1 : -1;
      this.highlight((this.highlighted + step + this.shown.length) % this.shown.length);
    } else if (e.key === 'Enter') {
      // Commit here rather than letting the panel close on Enter
      e.preventDefault();
      e.stopPropagation();
      if (open && this.highlighted >= 0) this.pick(this.shown[this.highlighted]);
      else this.commitTyped();
      this.hideSuggestions();
    } else if (e.key === 'Escape' && open) {
      e.stopPropagation(); // close the list, not the panel
      this.hideSuggestions();
    }
  }

  private pick(choice: MissionChoice): void {
    this.missionInput.value = choice.mission;
    this.hideSuggestions();
    this.commit(choice.mission);
  }

  private commitTyped(): void {
    this.commit(this.missionInput.value);
  }

  private commit(name: string): void {
    const trimmed = name.trim();
    if (trimmed === this.committed || trimmed === this.mission.mission) return;
    this.committed = trimmed;
    void this.mission.choose(trimmed);
  }
}
