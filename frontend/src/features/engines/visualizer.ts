import type { StateManager, AppState } from '../../state/state-manager';
import { CurrentMission } from '../../state/current-mission';
import { EngineSerials } from '../../state/engine-serials';
import { createSettingsButton, toggleEngineSettings, type EngineGroup } from './settings-popover';
import type { EngineStatus } from '../../domain/engines/events';
import { engineLabel, type Vehicle } from '../../domain/engines/layout';
import { createStarshipDiagram, createSuperHeavyDiagram, getStatusFill, getStatusStroke } from './diagram';
import { createLegend } from './legend';
import { EngineTooltip } from './tooltip';
import { buildOverlayUrl, type OverlaySnapshot } from '../../domain/overlay/snapshot';
import { overlayWidget, type OverlayWidgetId } from '../../domain/overlay/widgets';
import { createObsButton, overlayBaseUrl, toggleObsPopover } from '../../ui/obs-popover';

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

/** How overlay snapshots key a vehicle's serials: the engine number after this prefix ("6" for ss_e6). */
const ENGINE_ID_PREFIX: Record<Vehicle, string> = { starship: 'ss_e', superheavy: 'sh_e' };

export interface EngineVisualizerOptions {
  /** The diagrams to draw, in order. Default both, Super Heavy first. */
  vehicles?: readonly Vehicle[];
  /**
   * False leaves out the chrome (overlays): vehicle titles, settings gears,
   * OBS buttons and the legend. Default true.
   */
  chrome?: boolean;
  /** Each vehicle's serials. Default the localStorage-backed stores. */
  serials?: Record<Vehicle, EngineSerials>;
  /**
   * The mission the serials belong to. Default one built on the serials;
   * null builds none (overlays: CurrentMission writes the mission's file).
   */
  mission?: CurrentMission | null;
  /** Mission, ship and booster names to report when there is no mission (overlays). */
  missionNames?: MissionNames;
}

type MissionNames = NonNullable<OverlaySnapshot['m']>;

/** The panel a vehicle's gear opens, and what its OBS button offers. */
interface VehicleSection {
  heading: string;
  name: string;
  widget: OverlayWidgetId;
  panel: { title: string; groups: EngineGroup[]; vehicle: Vehicle; wide?: boolean };
}

const SECTIONS: Record<Vehicle, VehicleSection> = {
  superheavy: {
    heading: 'SUPER HEAVY',
    name: 'Super Heavy',
    widget: 'engines-superheavy',
    panel: { title: 'Super Heavy engines', groups: SUPERHEAVY_ENGINE_GROUPS, vehicle: 'superheavy', wide: true },
  },
  starship: {
    heading: 'STARSHIP',
    name: 'Starship',
    widget: 'engines-starship',
    panel: { title: 'Starship engines', groups: STARSHIP_ENGINE_GROUPS, vehicle: 'starship' },
  },
};

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
  /** The mission, ship and booster those serials belong to, saved to the mission's file; null in overlays. */
  private readonly mission: CurrentMission | null;
  private readonly missionNames: MissionNames;
  private readonly vehicles: readonly Vehicle[];
  private readonly chrome: boolean;
  private unsubscribeSerials: (() => void)[] = [];
  private tooltip!: EngineTooltip;

  constructor(container: HTMLElement, stateManager: StateManager, options: EngineVisualizerOptions = {}) {
    this.container = container;
    this.stateManager = stateManager;
    this.serials = options.serials ?? {
      starship: new EngineSerials('starship'),
      superheavy: new EngineSerials('superheavy'),
    };
    this.mission = options.mission === undefined ? new CurrentMission(this.serials) : options.mission;
    this.missionNames = options.missionNames ?? { n: '', ss: '', sh: '' };
    this.vehicles = options.vehicles ?? ['superheavy', 'starship'];
    this.chrome = options.chrome ?? true;

    this.render();
    this.subscribeToState();
    this.unsubscribeSerials = this.vehicles.map((vehicle) =>
      this.serials[vehicle].subscribe(() => this.labelEngines(vehicle))
    );
    this.update(this.stateManager.getState());
  }

  /**
   * One vehicle's overlay settings: the mission, ship and booster names and
   * the vehicle's serials by engine number.
   */
  overlaySnapshot(vehicle: Vehicle): OverlaySnapshot {
    const m: MissionNames = this.mission
      ? {
          n: this.mission.mission,
          ss: this.mission.vehicleName('starship'),
          sh: this.mission.vehicleName('superheavy'),
        }
      : { ...this.missionNames };
    const prefix = ENGINE_ID_PREFIX[vehicle];
    const e: Record<string, string> = {};
    for (const [id, serial] of Object.entries(this.serials[vehicle].snapshot())) {
      if (id.startsWith(prefix)) e[id.slice(prefix.length)] = serial;
    }
    return { v: 1, m, e };
  }

  /**
   * Build the DOM structure for the engine visualizer.
   */
  private render(): void {
    this.container.innerHTML = '';
    this.container.classList.add('engine-visualizer');
    this.tooltip = new EngineTooltip();

    for (const vehicle of this.vehicles) {
      const section = this.createSection(SECTIONS[vehicle]);
      const diagram = vehicle === 'superheavy' ? createSuperHeavyDiagram() : createStarshipDiagram();
      if (vehicle === 'superheavy') this.superheavyCircles = diagram.circles;
      else this.starshipCircles = diagram.circles;
      section.appendChild(diagram.svg);
      this.tooltip.attach(diagram.svg);
      this.container.appendChild(section);
    }
    for (const vehicle of this.vehicles) this.labelEngines(vehicle);

    if (this.chrome) this.container.appendChild(createLegend());
  }

  /** A vehicle's section with, unless chrome is off, its title, gear and OBS button. */
  private createSection({ heading, name, widget, panel }: VehicleSection): HTMLElement {
    const section = document.createElement('div');
    section.className = 'engine-visualizer__section';
    if (!this.chrome) return section;

    const title = document.createElement('h3');
    title.className = 'engine-visualizer__title';
    title.textContent = heading;
    section.appendChild(title);
    section.appendChild(this.createSettingsButton(name, panel));
    section.appendChild(this.createObsButton(name, panel.vehicle, widget));
    return section;
  }

  /** Gear opening a vehicle's engine settings; both vehicles share the mission. */
  private createSettingsButton(
    vehicleName: string,
    panel: { title: string; groups: EngineGroup[]; vehicle: Vehicle; wide?: boolean }
  ): HTMLButtonElement {
    const button = createSettingsButton(vehicleName);
    const mission = this.mission;
    button.addEventListener('click', () => {
      if (mission) toggleEngineSettings(button, { ...panel, serials: this.serials[panel.vehicle], mission });
    });
    return button;
  }

  /** OBS button offering this vehicle's diagram as an overlay. */
  private createObsButton(vehicleName: string, vehicle: Vehicle, widget: OverlayWidgetId): HTMLButtonElement {
    const button = createObsButton(`${vehicleName} engines`);
    button.classList.add('engine-visualizer__obs');
    button.addEventListener('click', () =>
      toggleObsPopover(button, {
        title: `${vehicleName} engines`,
        widgets: [overlayWidget(widget)],
        selected: widget,
        urlFor: (id) => buildOverlayUrl(overlayBaseUrl(), id, this.overlaySnapshot(vehicle)),
      })
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
