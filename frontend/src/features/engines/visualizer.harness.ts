/**
 * Test harness shared by the engine visualizer's test files (not app code):
 * a visualizer whose mission files live in a FakeMissionsApi, rebuilt for
 * every test, and helpers to reach its diagrams, settings panel and tooltip.
 *
 * The settings panel is a module-level singleton and workers are shared
 * (isolate: false): a file that clears the body would leave the next file a
 * detached panel. So each file calling useVisualizer() loads a private copy
 * of the visualizer (and with it the panel) and removes that panel when done.
 */
import { beforeAll, afterAll, beforeEach, afterEach, vi, type MockInstance } from 'vitest';
import { CurrentMission } from '../../state/current-mission';
import { EngineSerials } from '../../state/engine-serials';
import { FakeMissionsApi } from '../../api/fake-missions';
import { StateManager } from '../../state/state-manager';
import type { EngineVisualizer } from './visualizer';

let Visualizer: typeof EngineVisualizer;
let container: HTMLElement;
let visualizer: EngineVisualizer;
/** Where the visualizer's mission files live. */
export let api: FakeMissionsApi;
/** The visualizer's current mission, so tests can wait for its saves. */
export let currentMission: CurrentMission;
/** Every mission-list request; the field reloads the list when it gets focus. */
let listSpy: MockInstance<FakeMissionsApi['list']>;

/** A visualizer whose mission files live in `api`, not on a backend. */
function createVisualizer(): EngineVisualizer {
  const serials = { starship: new EngineSerials('starship'), superheavy: new EngineSerials('superheavy') };
  currentMission = new CurrentMission(serials, api, 0);
  return new Visualizer(container, new StateManager(), { serials, mission: currentMission });
}

/** Registers the hooks that give every test in the calling file a fresh visualizer. */
export function useVisualizer(): void {
  beforeAll(async () => {
    vi.resetModules();
    Visualizer = (await import('./visualizer')).EngineVisualizer;
  });

  afterAll(() => {
    document.querySelectorAll('.engine-settings').forEach((el) => el.remove());
    vi.resetModules();
  });

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
}

/** Destroys the visualizer and builds a new one, e.g. to load stored serials. */
export function reload(): void {
  visualizer.destroy();
  container.replaceChildren();
  visualizer = createVisualizer();
}

export function destroyVisualizer(): void {
  visualizer.destroy();
}

/** Builds a new visualizer in the same container, e.g. after destroyVisualizer(). */
export function rebuildVisualizer(): void {
  visualizer = createVisualizer();
}

/**
 * Waits for every mission list requested so far and for the mission field to
 * take it in: the field awaited the same promise first, so it has its choices.
 */
export async function choicesLoaded(): Promise<void> {
  await Promise.allSettled(listSpy.mock.results.map((r) => r.value));
}

/** The status line of the open settings panel. */
export function panelStatus(): string {
  return panel().querySelector('.engine-settings__status')!.textContent!;
}

export const E6 = [15, 15] as const;
export const E1 = [42, 21] as const;

/** One engine circle's group, located by its position in the vehicle's diagram. */
export function engineGroup(vehicle: string, [cx, cy]: readonly [number, number]): SVGGElement {
  const svg = container.querySelector(`[aria-label="${vehicle} engine status diagram"]`)!;
  return svg.querySelector(`circle[cx="${cx}"][cy="${cy}"]`)!.parentElement as unknown as SVGGElement;
}

export function starshipGroup(at: readonly [number, number]): SVGGElement {
  return engineGroup('Starship', at);
}

export function starshipEngine(at: readonly [number, number]) {
  const group = starshipGroup(at);
  return { text: group.querySelector('text')!.textContent, ariaLabel: group.getAttribute('aria-label') };
}

/** The visualizer's container. */
export function root(): HTMLElement {
  return container;
}

export function gear(vehicle = 'Starship'): HTMLButtonElement {
  return container.querySelector<HTMLButtonElement>(`[aria-label="${vehicle} engine settings"]`)!;
}

export function panel(): HTMLElement {
  return document.querySelector<HTMLElement>('.engine-settings')!;
}

export function field(engineId: string): HTMLInputElement {
  return panel().querySelector<HTMLInputElement>(`[data-engine="${engineId}"]`)!;
}

export function type(input: HTMLInputElement, value: string): void {
  input.value = value;
  input.dispatchEvent(new Event('input'));
}

export function tooltip(): HTMLElement {
  return document.querySelector<HTMLElement>('.engine-tooltip')!;
}

export function hover(at: readonly [number, number], vehicle = 'Starship'): void {
  engineGroup(vehicle, at).querySelector('circle')!.dispatchEvent(new Event('pointerover', { bubbles: true }));
}
