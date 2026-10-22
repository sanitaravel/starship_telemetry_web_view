/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach, afterEach, vi, type MockInstance } from 'vitest';
import { EngineVisualizer } from './engine-visualizer';
import { CurrentMission } from './current-mission';
import { EngineSerials } from './engine-serials';
import { FakeMissionsApi } from './fake-missions-api';
import { StateManager } from './state';

let container: HTMLElement;
let visualizer: EngineVisualizer;
let api: FakeMissionsApi;
/** The visualizer's current mission, so tests can wait for its saves. */
let currentMission: CurrentMission;
/** Every mission-list request; the field reloads the list when it gets focus. */
let listSpy: MockInstance<FakeMissionsApi['list']>;

/** A visualizer whose mission files live in `api`, not on a backend. */
function createVisualizer(): EngineVisualizer {
  const serials = { starship: new EngineSerials('starship'), superheavy: new EngineSerials('superheavy') };
  currentMission = new CurrentMission(serials, api, 0);
  return new EngineVisualizer(container, new StateManager(), serials, currentMission);
}

/**
 * Waits for every mission list requested so far and for the mission field to
 * take it in: the field awaited the same promise first, so it has its choices.
 */
async function choicesLoaded(): Promise<void> {
  await Promise.allSettled(listSpy.mock.results.map((r) => r.value));
}

/** The status line of the open settings panel. */
function panelStatus(): string {
  return panel().querySelector('.engine-settings__status')!.textContent!;
}

const E6 = [15, 15] as const;
const E1 = [42, 21] as const;

/** One engine circle's group, located by its position in the vehicle's diagram. */
function engineGroup(vehicle: string, [cx, cy]: readonly [number, number]): SVGGElement {
  const svg = container.querySelector(`[aria-label="${vehicle} engine status diagram"]`)!;
  return svg.querySelector(`circle[cx="${cx}"][cy="${cy}"]`)!.parentElement as unknown as SVGGElement;
}

function starshipGroup(at: readonly [number, number]): SVGGElement {
  return engineGroup('Starship', at);
}

function starshipEngine(at: readonly [number, number]) {
  const group = starshipGroup(at);
  return { text: group.querySelector('text')!.textContent, ariaLabel: group.getAttribute('aria-label') };
}

function gear(vehicle = 'Starship'): HTMLButtonElement {
  return container.querySelector<HTMLButtonElement>(`[aria-label="${vehicle} engine settings"]`)!;
}

function panel(): HTMLElement {
  return document.querySelector<HTMLElement>('.engine-settings')!;
}

function field(engineId: string): HTMLInputElement {
  return panel().querySelector<HTMLInputElement>(`[data-engine="${engineId}"]`)!;
}

function type(input: HTMLInputElement, value: string): void {
  input.value = value;
  input.dispatchEvent(new Event('input'));
}

function tooltip(): HTMLElement {
  return document.querySelector<HTMLElement>('.engine-tooltip')!;
}

function hover(at: readonly [number, number], vehicle = 'Starship'): void {
  engineGroup(vehicle, at).querySelector('circle')!.dispatchEvent(new Event('pointerover', { bubbles: true }));
}

function reload(): void {
  visualizer.destroy();
  container.replaceChildren();
  visualizer = createVisualizer();
}

beforeEach(() => {
  localStorage.clear();
  api = new FakeMissionsApi();
  listSpy = vi.spyOn(api, 'list');
  container = document.createElement('div');
  document.body.appendChild(container);
  visualizer = createVisualizer();
});

afterEach(() => {
  document.dispatchEvent(new Event('pointerdown')); // closes the panel
  visualizer.destroy();
  container.remove();
});

describe('Starship engine settings panel', () => {
  it('has a gear on each diagram', () => {
    const buttons = [...container.querySelectorAll('.engine-settings-btn')];
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Super Heavy engine settings',
      'Starship engine settings',
    ]);
  });

  it('opens with one field per engine, grouped by type, each after a fixed R', () => {
    gear().click();

    expect(panel().hidden).toBe(false);
    expect(gear().getAttribute('aria-expanded')).toBe('true');
    const groups = [...panel().querySelectorAll('fieldset.engine-settings__group')].map((g) => ({
      title: g.querySelector('legend')!.textContent,
      engines: [...g.querySelectorAll('input')].map((i) => i.dataset.engine),
    }));
    expect(groups).toEqual([
      { title: 'Sea level', engines: ['ss_e1', 'ss_e2', 'ss_e3'] },
      { title: 'Vacuum', engines: ['ss_e4', 'ss_e5', 'ss_e6'] },
    ]);
    const prefixes = [...panel().querySelectorAll('.engine-settings__prefix')].map((p) => p.textContent);
    expect(prefixes).toEqual(['R', 'R', 'R', 'R', 'R', 'R']);
  });

  it('saves the typed digits after the R, and a typed R is not doubled', () => {
    gear().click();
    type(field('ss_e6'), '576');
    type(field('ss_e5'), 'r575');

    const saved = new EngineSerials('starship');
    expect(saved.get('ss_e6')).toBe('R576');
    expect(saved.get('ss_e5')).toBe('R575');
  });

  it('shows saved serials without their R when opened', () => {
    new EngineSerials('starship').set('ss_e5', 'R575');
    reload();

    gear().click();
    expect(field('ss_e5').value).toBe('575');
    expect(field('ss_e4').value).toBe('');
  });

  it('an emptied field removes the serial', () => {
    gear().click();
    type(field('ss_e6'), '576');
    type(field('ss_e6'), '');

    expect(new EngineSerials('starship').get('ss_e6')).toBeUndefined();
  });

  it('Clear all removes every serial', () => {
    gear().click();
    const clear = [...panel().querySelectorAll('button')].find((b) => b.textContent === 'Clear all')!;
    expect(clear.disabled).toBe(true);

    type(field('ss_e1'), '101');
    type(field('ss_e6'), '576');
    expect(clear.disabled).toBe(false);
    clear.click();

    expect(new EngineSerials('starship').isEmpty).toBe(true);
    expect(field('ss_e6').value).toBe('');
    expect(clear.disabled).toBe(true);
  });

  it('closes on Escape, on Done and on a second click of the gear', () => {
    gear().click();
    field('ss_e1').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(panel().hidden).toBe(true);
    expect(gear().getAttribute('aria-expanded')).toBe('false');

    gear().click();
    [...panel().querySelectorAll('button')].find((b) => b.textContent === 'Done')!.click();
    expect(panel().hidden).toBe(true);

    gear().click();
    gear().click();
    expect(panel().hidden).toBe(true);
  });
});

describe('Starship engine serials on the diagram', () => {
  it('circles keep their E-numbers when a serial is set', () => {
    gear().click();
    type(field('ss_e6'), '576');

    expect(starshipEngine(E6)).toEqual({ text: 'E6', ariaLabel: 'E6, serial R576' });
    expect(starshipEngine(E1)).toEqual({ text: 'E1', ariaLabel: 'E1' });
  });

  it('hovering an engine with a serial shows it in the tooltip', () => {
    new EngineSerials('starship').set('ss_e6', 'R576');
    reload();

    expect(tooltip().hidden).toBe(true);
    hover(E6);
    expect(tooltip().hidden).toBe(false);
    expect(tooltip().textContent).toBe('E6R576');
    expect(tooltip().querySelector('.engine-tooltip__serial')!.textContent).toBe('R576');

    starshipGroup(E6).ownerSVGElement!.dispatchEvent(new Event('pointerleave'));
    expect(tooltip().hidden).toBe(true);
  });

  it('shows no tooltip over an engine without a serial', () => {
    new EngineSerials('starship').set('ss_e6', 'R576');
    reload();

    hover(E1);
    expect(tooltip().hidden).toBe(true);
  });

  it('an open tooltip follows a serial edited meanwhile', () => {
    gear().click();
    type(field('ss_e6'), '576');
    hover(E6);
    expect(tooltip().textContent).toBe('E6R576');

    type(field('ss_e6'), '577');
    expect(tooltip().textContent).toBe('E6R577');

    type(field('ss_e6'), '');
    expect(tooltip().hidden).toBe(true);
  });

  it('removes its tooltip when destroyed', () => {
    expect(document.querySelectorAll('.engine-tooltip')).toHaveLength(1);
    visualizer.destroy();
    expect(document.querySelectorAll('.engine-tooltip')).toHaveLength(0);
    visualizer = createVisualizer(); // for afterEach
  });
});

describe('mission and ship in the settings panel', () => {
  function missionField(): HTMLInputElement {
    return panel().querySelector<HTMLInputElement>('[data-field="mission"]')!;
  }

  function shipField(): HTMLInputElement {
    return panel().querySelector<HTMLInputElement>('[data-field="ship"]')!;
  }

  function suggestions(): string[] {
    const list = panel().querySelector<HTMLElement>('.engine-settings__suggestions')!;
    if (list.hidden) return [];
    return [...list.querySelectorAll('li')].map((li) => li.textContent!);
  }

  function key(target: HTMLElement, name: string): void {
    target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
  }

  function status(): string {
    return panelStatus();
  }

  beforeEach(() => {
    api.files.set('ift-14', {
      id: 'ift-14',
      mission: 'IFT 14',
      ship: 'S33',
      booster: '',
      starship_engines: { ss_e1: 'R101', ss_e6: 'R576' },
      updated_at: null,
    });
    api.flights = [
      { name: 'IFT 13', filename: 'ift-13' },
      { name: 'IFT 14', filename: 'ift-14' }, // already saved: listed once
    ];
  });

  it('opens on the mission field and asks for a name before saving', async () => {
    gear().click();
    await choicesLoaded();

    expect(document.activeElement).toBe(missionField());
    expect(status()).toBe('Name the mission to save it to a file');
  });

  it('does not drop the list down just because the panel opened', async () => {
    gear().click();
    await choicesLoaded();

    expect(document.activeElement).toBe(missionField());
    expect(suggestions()).toEqual([]);
    expect(missionField().getAttribute('aria-expanded')).toBe('false');
  });

  it('a click on the field or arrow down lists every mission', async () => {
    gear().click();
    await choicesLoaded();
    missionField().click();
    expect(suggestions()).toEqual(['IFT 14S33', 'IFT 13recorded flight']);

    key(missionField(), 'Escape');
    expect(suggestions()).toEqual([]);
    key(missionField(), 'ArrowDown');
    expect(suggestions()).toEqual(['IFT 14S33', 'IFT 13recorded flight']);
  });

  it('suggests saved missions and recorded flights matching what is typed', async () => {
    gear().click();
    await choicesLoaded();

    type(missionField(), '13');
    expect(suggestions()).toEqual(['IFT 13recorded flight']);

    type(missionField(), 'flight 99');
    expect(suggestions()).toEqual([]);
  });

  it('picking a saved mission loads its ship and serials', async () => {
    gear().click();
    await choicesLoaded();
    type(missionField(), 'ift 1');
    key(missionField(), 'ArrowDown');
    key(missionField(), 'Enter');
    // Loading the file ends by showing where it came from
    await vi.waitFor(() => expect(status()).toBe('File: missions/engines_ift-14.json'));

    expect(missionField().value).toBe('IFT 14');
    expect(shipField().value).toBe('S33');
    expect(field('ss_e1').value).toBe('101');
    expect(field('ss_e6').value).toBe('576');
    expect(starshipEngine(E6).ariaLabel).toBe('E6, serial R576');
    expect(status()).toBe('File: missions/engines_ift-14.json'); // read, not rewritten
    expect(api.saves).toHaveLength(0);
    expect(panel().hidden).toBe(false); // Enter picked, it did not close the panel
  });

  it('clicking a suggestion picks it', async () => {
    gear().click();
    await choicesLoaded();
    missionField().click();
    panel().querySelector<HTMLElement>('[data-mission="ift-14"]')!.click();

    await vi.waitFor(() => expect(shipField().value).toBe('S33'));
  });

  it('a new mission name gets its own file with the ship and serials typed in', async () => {
    gear().click();
    await choicesLoaded();
    type(missionField(), 'IFT 15');
    key(missionField(), 'Enter');
    await vi.waitFor(() => expect(api.files.has('ift-15')).toBe(true)); // the new mission's file exists
    shipField().value = 'S35';
    shipField().dispatchEvent(new Event('change'));
    type(field('ss_e4'), '404');
    await currentMission.flush();

    expect(api.files.get('ift-15')).toMatchObject({
      mission: 'IFT 15',
      ship: 'S35',
      starship_engines: { ss_e4: 'R404' },
    });
    expect(status()).toBe('Saved to missions/engines_ift-15.json');
    expect(api.files.get('ift-14')!.starship_engines).toEqual({ ss_e1: 'R101', ss_e6: 'R576' });
  });

  it('typing part of a saved name loads nothing until it is committed', async () => {
    gear().click();
    await choicesLoaded();
    type(missionField(), 'IFT 1'); // on the way to "IFT 15"
    // Nothing to wait for if nothing gets committed; a macrotask lets any
    // wrongly started load (all promise work, no timers) finish first.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await currentMission.flush();

    expect(shipField().value).toBe('');
    expect(field('ss_e1').value).toBe('');
  });

  it('Escape closes the suggestions first, then the panel', async () => {
    gear().click();
    await choicesLoaded();
    missionField().click();
    expect(suggestions()).not.toEqual([]);

    key(missionField(), 'Escape');
    expect(suggestions()).toEqual([]);
    expect(panel().hidden).toBe(false);

    key(missionField(), 'Escape');
    expect(panel().hidden).toBe(true);
  });

  it('shows when the backend cannot be reached', async () => {
    api.offline = true;
    gear().click();
    await choicesLoaded();
    type(missionField(), 'IFT 15');
    key(missionField(), 'Enter');

    await vi.waitFor(() => expect(status()).toBe('Not saved: backend unreachable'));
  });
});

describe('mission list freshness', () => {
  it('offers a mission created since the panel opened', async () => {
    gear().click();
    await choicesLoaded();
    const mission = panel().querySelector<HTMLInputElement>('[data-field="mission"]')!;
    type(mission, 'IFT 16');
    mission.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await vi.waitFor(() => expect(api.files.has('ift-16')).toBe(true)); // created since the panel opened

    mission.blur();
    mission.focus();
    await choicesLoaded();
    mission.click();

    const offered = [...panel().querySelectorAll<HTMLElement>('.engine-settings__suggestion')].map((li) => li.dataset.mission);
    expect(offered).toContain('ift-16');
  });
});

describe('Super Heavy engine settings panel', () => {
  const SH_E1 = [56, 43] as const;

  it('lists all 33 engines ring by ring in a wide panel', () => {
    gear('Super Heavy').click();

    expect(panel().getAttribute('aria-label')).toBe('Super Heavy engines');
    expect(panel().classList.contains('engine-settings--wide')).toBe(true);
    const groups = [...panel().querySelectorAll('fieldset.engine-settings__group')].map((g) => ({
      title: g.querySelector('legend')!.textContent,
      count: g.querySelectorAll('input').length,
      first: g.querySelector('input')!.dataset.engine,
    }));
    expect(groups).toEqual([
      { title: 'Inner ring', count: 3, first: 'sh_e1' },
      { title: 'Middle ring', count: 10, first: 'sh_e4' },
      { title: 'Outer ring', count: 20, first: 'sh_e14' },
    ]);
  });

  it('asks for the booster, not the ship', () => {
    gear('Super Heavy').click();

    expect(panel().querySelector('[data-field="ship"]')).toBeNull();
    const booster = panel().querySelector<HTMLInputElement>('[data-field="booster"]')!;
    expect(booster.getAttribute('aria-label')).toBe('Booster name');
    expect(booster.placeholder).toBe('B16');
  });

  it('the Starship panel is the narrow one again after Super Heavy’s', () => {
    gear('Super Heavy').click();
    gear().click();

    expect(panel().classList.contains('engine-settings--wide')).toBe(false);
    expect(panel().querySelectorAll('input[data-engine]')).toHaveLength(6);
  });

  it('serials go to Super Heavy’s own store and show on hover', () => {
    gear('Super Heavy').click();
    type(field('sh_e1'), '301');

    expect(new EngineSerials('superheavy').get('sh_e1')).toBe('R301');
    expect(new EngineSerials('starship').isEmpty).toBe(true);
    expect(engineGroup('Super Heavy', SH_E1).getAttribute('aria-label')).toBe('E1, serial R301');
    hover(SH_E1, 'Super Heavy');
    expect(tooltip().textContent).toBe('E1R301');
    expect(document.querySelectorAll('.engine-tooltip')).toHaveLength(1); // shared by both diagrams
  });

  it('choosing a mission in either panel loads it for both vehicles', async () => {
    api.files.set('ift-14', {
      id: 'ift-14',
      mission: 'IFT 14',
      ship: 'S41',
      booster: 'B15',
      starship_engines: { ss_e6: 'R171' },
      superheavy_engines: { sh_e1: 'R301' },
      updated_at: null,
    });
    gear('Super Heavy').click();
    await choicesLoaded();
    const mission = panel().querySelector<HTMLInputElement>('[data-field="mission"]')!;
    type(mission, 'IFT 14');
    mission.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    // Loading the file ends by showing where it came from
    await vi.waitFor(() => expect(panelStatus()).toBe('File: missions/engines_ift-14.json'));

    expect(panel().querySelector<HTMLInputElement>('[data-field="booster"]')!.value).toBe('B15');
    expect(field('sh_e1').value).toBe('301');
    expect(starshipEngine(E6).ariaLabel).toBe('E6, serial R171');

    gear().click();
    expect(panel().querySelector<HTMLInputElement>('[data-field="mission"]')!.value).toBe('IFT 14');
    expect(panel().querySelector<HTMLInputElement>('[data-field="ship"]')!.value).toBe('S41');
  });

  it('suggestions show both the ship and the booster of a saved mission', async () => {
    api.files.set('ift-14', {
      id: 'ift-14',
      mission: 'IFT 14',
      ship: 'S41',
      booster: 'B15',
      starship_engines: {},
      updated_at: null,
    });
    gear('Super Heavy').click();
    await choicesLoaded();
    panel().querySelector<HTMLInputElement>('[data-field="mission"]')!.click();

    expect(panel().querySelector('.engine-settings__suggestion')!.textContent).toBe('IFT 14S41 · B15');
  });
});
