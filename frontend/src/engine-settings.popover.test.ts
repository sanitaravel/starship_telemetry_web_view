/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from 'vitest';
import type { EngineGroup } from './engine-settings';

// The settings panel is a module-level singleton and workers are shared
// (isolate: false). Load a private copy of the module and drop it afterwards,
// so the panel created here (and later detached by other files clearing the
// body) is never the one engine-visualizer.test.ts gets.
let settings: typeof import('./engine-settings');
let serialsModule: typeof import('./state/engine-serials');

beforeAll(async () => {
  vi.resetModules();
  settings = await import('./engine-settings');
  serialsModule = await import('./state/engine-serials');
});

afterAll(() => {
  document.dispatchEvent(new Event('pointerdown'));
  ownPanel?.remove();
  vi.resetModules();
});

/** The panel this file's module copy created, removed once the file is done. */
let ownPanel: HTMLElement | undefined;

const GROUPS: EngineGroup[] = [
  { title: 'Sea level', engines: [{ id: 'ss_e1', label: 'E1' }, { id: 'ss_e2', label: 'E2' }] },
];

function setup() {
  const button = settings.createSettingsButton('Starship');
  button.classList.add('test-settings-button');
  document.body.appendChild(button);
  const serials = new serialsModule.EngineSerials('starship');
  settings.toggleEngineSettings(button, { title: 'Starship engines', groups: GROUPS, serials });
  // The open one: another module copy's panel may still sit in the body, closed
  const panel = [...document.querySelectorAll<HTMLElement>('.engine-settings')].find((p) => !p.hidden)!;
  ownPanel = panel;
  const inputs = [...panel.querySelectorAll<HTMLInputElement>('.engine-settings__input')];
  return { button, serials, panel, inputs };
}

beforeEach(() => localStorage.clear());

afterEach(() => {
  document.dispatchEvent(new Event('pointerdown'));
  document.querySelectorAll('.test-settings-button, .test-outside').forEach((el) => el.remove());
});

describe('engine settings popover dismissal', () => {
  it('opens with focus on the first serial field', () => {
    const { button, panel, inputs } = setup();
    expect(panel.hidden).toBe(false);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(inputs).toHaveLength(2);
    expect(document.activeElement).toBe(inputs[0]);
  });

  it('closes on Escape, returns focus to the gear button and keeps Escape from outer handlers', () => {
    const { button, panel, inputs } = setup();
    const outer = vi.fn();
    document.body.addEventListener('keydown', outer);
    inputs[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    document.body.removeEventListener('keydown', outer);
    expect(panel.hidden).toBe(true);
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(button);
    expect(outer).not.toHaveBeenCalled();
  });

  it('closes on Enter in a serial field, keeping the typed serial and returning focus', () => {
    const { button, panel, inputs, serials } = setup();
    inputs[0].value = '576';
    inputs[0].dispatchEvent(new Event('input'));
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    inputs[0].dispatchEvent(enter);
    expect(enter.defaultPrevented).toBe(true);
    expect(panel.hidden).toBe(true);
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(button);
    expect(serials.get('ss_e1')).toBe('R576');
  });

  it('does not close on Enter on a panel button', () => {
    const { panel } = setup();
    const clear = panel.querySelector<HTMLButtonElement>('.engine-settings__button')!;
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    clear.dispatchEvent(enter);
    expect(enter.defaultPrevented).toBe(false);
    expect(panel.hidden).toBe(false);
  });

  it('closes on a press outside without moving focus', () => {
    const { button, panel, inputs } = setup();
    expect(document.activeElement).toBe(inputs[0]);
    const outside = document.createElement('div');
    outside.className = 'test-outside';
    document.body.appendChild(outside);
    outside.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(panel.hidden).toBe(true);
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).not.toBe(button);
  });

  it('stays open on a press on the gear button, its icon, or inside the panel', () => {
    const { button, panel, inputs } = setup();
    button.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    button.querySelector('path')!.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    inputs[1].dispatchEvent(new Event('pointerdown', { bubbles: true }));
    panel.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(panel.hidden).toBe(false);
    expect(button.getAttribute('aria-expanded')).toBe('true');
  });
});
