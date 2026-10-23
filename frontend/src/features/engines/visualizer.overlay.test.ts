/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from 'vitest';
import { EngineSerials } from '../../state/engine-serials';
import { CurrentMission } from '../../state/current-mission';
import { FakeMissionsApi } from '../../api/fake-missions';
import { StateManager } from '../../state/state-manager';
import { decodeSnapshot } from '../../domain/overlay/snapshot';
import type { EngineVisualizer } from './visualizer';

// The OBS popover and the settings panel are module-level singletons and
// workers are shared (isolate: false): load private copies and remove their
// panels when done.
let Visualizer: typeof EngineVisualizer;

beforeAll(async () => {
  document.dispatchEvent(new Event('pointerdown'));
  vi.resetModules();
  Visualizer = (await import('./visualizer')).EngineVisualizer;
});

afterAll(() => {
  document.dispatchEvent(new Event('pointerdown'));
  document.querySelectorAll('.obs-popover, .engine-settings').forEach((el) => el.remove());
  vi.resetModules();
});

let container: HTMLElement;
let visualizer: EngineVisualizer | undefined;

beforeEach(() => {
  localStorage.clear();
  container = document.createElement('div');
  document.body.appendChild(container);
});

afterEach(() => {
  document.dispatchEvent(new Event('pointerdown'));
  visualizer?.destroy();
  visualizer = undefined;
  container.remove();
});

function memorySerials(starship: Record<string, string> = {}, superheavy: Record<string, string> = {}) {
  return {
    starship: new EngineSerials('starship', { persist: false, initial: starship }),
    superheavy: new EngineSerials('superheavy', { persist: false, initial: superheavy }),
  };
}

describe('EngineVisualizer without chrome', () => {
  it('draws one vehicle with no title, gear, OBS button or legend', () => {
    visualizer = new Visualizer(container, new StateManager(), {
      vehicles: ['starship'],
      chrome: false,
      serials: memorySerials(),
      mission: null,
    });
    expect(container.querySelectorAll('svg')).toHaveLength(1);
    expect(container.querySelector('[aria-label="Starship engine status diagram"]')).not.toBeNull();
    expect(container.querySelectorAll('button, select, input, h3')).toHaveLength(0);
    expect(container.querySelector('.engine-visualizer__legend')).toBeNull();
  });

  it('puts the injected serials on the circles', () => {
    visualizer = new Visualizer(container, new StateManager(), {
      vehicles: ['superheavy'],
      chrome: false,
      serials: memorySerials({}, { sh_e12: 'R812' }),
      mission: null,
    });
    const group = container.querySelector<SVGGElement>('g[data-serial]');
    expect(group?.dataset.serial).toBe('R812');
    expect(group?.getAttribute('aria-label')).toContain('serial R812');
  });

  it('reports the snapshot names and serials when there is no mission', () => {
    visualizer = new Visualizer(container, new StateManager(), {
      vehicles: ['starship'],
      chrome: false,
      serials: memorySerials({ ss_e6: 'R576' }),
      mission: null,
      missionNames: { n: 'IFT 14', ss: 'S41', sh: 'B15' },
    });
    expect(visualizer.overlaySnapshot('starship')).toEqual({
      v: 1,
      m: { n: 'IFT 14', ss: 'S41', sh: 'B15' },
      e: { '6': 'R576' },
    });
  });
});

describe('EngineVisualizer OBS buttons', () => {
  it('has one per vehicle, whose URL carries that vehicle\'s serials and the mission', async () => {
    const serials = memorySerials({ ss_e6: 'R576' }, { sh_e1: 'R100', sh_e33: 'R133' });
    const mission = new CurrentMission(serials, new FakeMissionsApi(), 0);
    visualizer = new Visualizer(container, new StateManager(), { serials, mission });
    mission.setVehicleName('starship', 'S41');

    const buttons = [...container.querySelectorAll<HTMLButtonElement>('.obs-btn')];
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([
      'OBS Browser Source URL for Super Heavy engines',
      'OBS Browser Source URL for Starship engines',
    ]);

    buttons[0].click();
    const url = new URL(document.querySelector<HTMLInputElement>('.obs-popover:not([hidden]) .obs-popover__url')!.value);
    expect(url.searchParams.get('overlay')).toBe('engines-superheavy');
    expect(decodeSnapshot(url.searchParams.get('s'))).toEqual({
      v: 1,
      m: { n: '', ss: 'S41', sh: '' },
      e: { '1': 'R100', '33': 'R133' },
    });
    await mission.flush();
  });
});
