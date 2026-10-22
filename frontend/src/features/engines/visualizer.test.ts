/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi } from 'vitest';
import { EngineSerials } from '../../state/engine-serials';
import {
  useVisualizer,
  api,
  choicesLoaded,
  panelStatus,
  E6,
  engineGroup,
  starshipEngine,
  root,
  gear,
  panel,
  field,
  type,
  tooltip,
  hover,
  reload,
} from './visualizer.harness';

useVisualizer();

describe('Starship engine settings panel', () => {
  it('has a gear on each diagram', () => {
    const buttons = [...root().querySelectorAll('.engine-settings-btn')];
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
