/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  useVisualizer,
  api,
  currentMission,
  choicesLoaded,
  panelStatus,
  E6,
  starshipEngine,
  gear,
  panel,
  field,
  type,
} from './visualizer.harness';

useVisualizer();

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
