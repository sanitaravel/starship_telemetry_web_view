import type { StateManager, AppState } from '../../state/state-manager';
import { CurrentMission } from '../../state/current-mission';
import { EngineSerials } from '../../state/engine-serials';
import { createSettingsButton, toggleEngineSettings, type EngineGroup } from './settings-popover';
import type { EngineStatus } from '../../domain/engines/events';
import { engineLabel, type Vehicle } from '../../domain/engines/layout';
import { createStarshipDiagram, createSuperHeavyDiagram, getStatusFill, getStatusStroke } from './diagram';
import { createLegend } from './legend';
import { EngineTooltip } from './tooltip';

/** A settings panel group of engines `first`..`last` of a vehicle, e.g. ss_e1..ss_e3. */
function engineGroup(title: string, prefix: 'ss' | 'sh', first: number, last: number): EngineGroup {
  const engines = [];
  for (let n = first; n <= last; n++) {
    const id = `${prefix}_e${n}`;
    engines.push({ id, label: engineLabel(id) });
  }
  return { title, engines };
}

/** Starship's engines as the settings panel lists them. */
const STARSHIP_ENGINE_GROUPS: EngineGroup[] = [
  engineGroup('Sea level', 'ss', 1, 3),
  engineGroup('Vacuum', 'ss', 4, 6),
];

/** Super Heavy's engines as the settings panel lists them, ring by ring. */
const SUPERHEAVY_ENGINE_GROUPS: EngineGroup[] = [
  engineGroup('Inner ring', 'sh', 1, 3),
  engineGroup('Middle ring', 'sh', 4, 13),
  engineGroup('Outer ring', 'sh', 14, 33),
];

/**
 * Engine Visualizer UI component.
 * Renders SVG diagrams showing the spatial layout of engines for both
 * Super Heavy and Starship vehicles, color-coded by engine status.
 */
export class EngineVisualizer {
  private container: HTMLElement;
  private stateManager: StateManager;
  private unsubscribe: (() => void) | null = null;
  private lastRecord: AppState['latestTelemetry'] = null;
  /** Status each engine circle currently shows, to skip unchanged updates. */
  private renderedStatus: Map<SVGGElement, EngineStatus> = new Map();

  private superheavyCircles: Map<string, SVGGElement> = new Map();
  private starshipCircles: Map<string, SVGGElement> = new Map();
  /** Serial numbers entered for this mission's engines, per vehicle. */
  private readonly serials: Record<Vehicle, EngineSerials>;
  /** The mission, ship and booster those serials belong to, saved to the mission's file. */
  private readonly mission: CurrentMission;
  private unsubscribeSerials: (() => void)[] = [];
  private tooltip!: EngineTooltip;

  constructor(
    container: HTMLElement,
    stateManager: StateManager,
    serials: Record<Vehicle, EngineSerials> = {
      starship: new EngineSerials('starship'),
      superheavy: new EngineSerials('superheavy'),
    },
    mission: CurrentMission = new CurrentMission(serials)
  ) {
    this.container = container;
    this.stateManager = stateManager;
    this.serials = serials;
    this.mission = mission;

    this.render();
    this.subscribeToState();
    this.unsubscribeSerials = (['starship', 'superheavy'] as const).map((vehicle) =>
      serials[vehicle].subscribe(() => this.labelEngines(vehicle))
    );
    this.update(this.stateManager.getState());
  }

  /**
   * Build the DOM structure for the engine visualizer.
   */
  private render(): void {
    this.container.innerHTML = '';
    this.container.classList.add('engine-visualizer');

    // Super Heavy section
    const superheavySection = document.createElement('div');
    superheavySection.className = 'engine-visualizer__section';

    const superheavyTitle = document.createElement('h3');
    superheavyTitle.className = 'engine-visualizer__title';
    superheavyTitle.textContent = 'SUPER HEAVY';
    superheavySection.appendChild(superheavyTitle);

    superheavySection.appendChild(
      this.createSettingsButton('Super Heavy', {
        title: 'Super Heavy engines',
        groups: SUPERHEAVY_ENGINE_GROUPS,
        vehicle: 'superheavy',
        wide: true,
      })
    );

    const superheavy = createSuperHeavyDiagram();
    this.superheavyCircles = superheavy.circles;
    superheavySection.appendChild(superheavy.svg);

    // Starship section
    const starshipSection = document.createElement('div');
    starshipSection.className = 'engine-visualizer__section';

    const starshipTitle = document.createElement('h3');
    starshipTitle.className = 'engine-visualizer__title';
    starshipTitle.textContent = 'STARSHIP';
    starshipSection.appendChild(starshipTitle);

    starshipSection.appendChild(
      this.createSettingsButton('Starship', {
        title: 'Starship engines',
        groups: STARSHIP_ENGINE_GROUPS,
        vehicle: 'starship',
      })
    );

    const starship = createStarshipDiagram();
    this.starshipCircles = starship.circles;
    starshipSection.appendChild(starship.svg);

    this.tooltip = new EngineTooltip();
    this.tooltip.attach(superheavy.svg);
    this.tooltip.attach(starship.svg);
    this.labelEngines('superheavy');
    this.labelEngines('starship');

    this.container.appendChild(superheavySection);
    this.container.appendChild(starshipSection);

    // Legend
    const legend = createLegend();
    this.container.appendChild(legend);
  }

  /** Gear opening a vehicle's engine settings; both vehicles share the mission. */
  private createSettingsButton(
    vehicleName: string,
    panel: { title: string; groups: EngineGroup[]; vehicle: Vehicle; wide?: boolean }
  ): HTMLButtonElement {
    const button = createSettingsButton(vehicleName);
    button.addEventListener('click', () =>
      toggleEngineSettings(button, { ...panel, serials: this.serials[panel.vehicle], mission: this.mission })
    );
    return button;
  }

  /**
   * Attaches each of a vehicle's engine serials to its circle. The circle
   * keeps showing its E-number; the serial appears in the hover tooltip.
   */
  private labelEngines(vehicle: Vehicle): void {
    const circles = vehicle === 'starship' ? this.starshipCircles : this.superheavyCircles;
    for (const [id, group] of circles) {
      const serial = this.serials[vehicle].get(id);
      const label = group.dataset.label!;
      if (serial) {
        group.dataset.serial = serial;
        group.setAttribute('aria-label', `${label}, serial ${serial}`);
      } else {
        delete group.dataset.serial;
        group.setAttribute('aria-label', label);
      }
    }
    // Keep an open tooltip in step with the serial under the pointer
    this.tooltip.refresh();
  }

  /**
   * Subscribe to state changes.
   */
  private subscribeToState(): void {
    this.unsubscribe = this.stateManager.subscribe((state) => this.update(state));
  }

  /**
   * Update engine visualizations based on new state.
   */
  private update(state: AppState): void {
    const record = state.latestTelemetry;
    // Frame and status messages also notify; engines only change with telemetry
    if (record === this.lastRecord && record !== null) return;
    this.lastRecord = record;

    if (!record) {
      this.resetAllEngines();
      return;
    }

    // Update Super Heavy engines
    for (const [key, circleGroup] of this.superheavyCircles) {
      const status: EngineStatus = record.superheavy_engines[key] ?? 'undetected';
      this.updateCircleStatus(circleGroup, status);
    }

    // Update Starship engines
    for (const [key, circleGroup] of this.starshipCircles) {
      const status: EngineStatus = record.starship_engines[key] ?? 'undetected';
      this.updateCircleStatus(circleGroup, status);
    }
  }

  /**
   * Update the fill/stroke of a circle group based on engine status.
   */
  private updateCircleStatus(group: SVGGElement, status: EngineStatus): void {
    if (this.renderedStatus.get(group) === status) return;
    const circle = group.querySelector('circle');
    if (circle) {
      circle.setAttribute('fill', getStatusFill(status));
      circle.setAttribute('stroke', getStatusStroke(status));
      this.renderedStatus.set(group, status);
    }
  }

  /**
   * Reset all engines to undetected state.
   */
  private resetAllEngines(): void {
    for (const group of this.superheavyCircles.values()) {
      this.updateCircleStatus(group, 'undetected');
    }
    for (const group of this.starshipCircles.values()) {
      this.updateCircleStatus(group, 'undetected');
    }
  }

  /**
   * Cleanup subscriptions.
   */
  destroy(): void {
    if (this.unsubscribe) {
      this.unsubscribe();
      this.unsubscribe = null;
    }
    for (const unsubscribe of this.unsubscribeSerials) unsubscribe();
    this.unsubscribeSerials = [];
    this.tooltip.remove();
  }
}
