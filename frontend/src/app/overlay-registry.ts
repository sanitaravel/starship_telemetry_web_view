import type { StateManager } from '../state/state-manager';
import { EngineSerials } from '../state/engine-serials';
import { FlightColors } from '../state/flight-colors';
import { ShipTracker } from '../state/ship-tracker';
import { fetchHistoricalTrajectories } from '../api/trajectories';
import type { Vehicle } from '../domain/engines/layout';
import type { OverlaySnapshot } from '../domain/overlay/snapshot';
import { GRAPH_SERIES, graphWidgetId, type OverlayWidgetId } from '../domain/overlay/widgets';
import { TelemetryDisplay } from '../features/telemetry-display';
import { MetDisplay } from '../features/met-display';
import { EngineVisualizer } from '../features/engines/visualizer';
import { TimeSeriesGraphs } from '../features/graphs';
import { ShipGlobe } from '../features/ship-globe';

/** What an overlay widget is mounted with. */
export interface OverlayDeps {
  stateManager: StateManager;
  snapshot: OverlaySnapshot;
}

export type OverlayMount = (container: HTMLElement, deps: OverlayDeps) => void;

/**
 * One vehicle's diagram. Serials live in memory only, seeded from the
 * snapshot; no CurrentMission, which would write the mission's file.
 */
function mountEngines(vehicle: Vehicle): OverlayMount {
  return (container, { stateManager, snapshot }) => {
    const prefix = vehicle === 'starship' ? 'ss_e' : 'sh_e';
    const own: Record<string, string> = {};
    for (const [n, serial] of Object.entries(snapshot.e ?? {})) own[`${prefix}${n}`] = serial;
    const serials: Record<Vehicle, EngineSerials> = {
      starship: new EngineSerials('starship', { persist: false, initial: vehicle === 'starship' ? own : {} }),
      superheavy: new EngineSerials('superheavy', { persist: false, initial: vehicle === 'superheavy' ? own : {} }),
    };
    new EngineVisualizer(container, stateManager, {
      vehicles: [vehicle],
      chrome: false,
      serials,
      mission: null,
      missionNames: snapshot.m,
    });
  };
}

/** The globe following the latest ship, recorded paths in the snapshot's colours. */
const mountGlobe: OverlayMount = (container, { snapshot }) => {
  container.classList.add('overlay-panel');
  const tracker = new ShipTracker();
  const colors = new FlightColors('trajectory', { persist: false, initial: snapshot.t ?? {} });
  const globe = new ShipGlobe(container, tracker, { chrome: false, hidden: snapshot.h ?? [], colors });
  tracker.start();
  void fetchHistoricalTrajectories().then((trajectories) => globe.setHistory(trajectories));
};

/** How to mount each overlay widget, with chrome off and memory-only settings. */
export const OVERLAY_REGISTRY: Record<OverlayWidgetId, OverlayMount> = {
  telemetry: (container, { stateManager }) => {
    container.classList.add('overlay-panel');
    new TelemetryDisplay(container, stateManager, { chrome: false });
  },
  met: (container, { stateManager }) => {
    new MetDisplay(container, stateManager);
  },
  'engines-superheavy': mountEngines('superheavy'),
  'engines-starship': mountEngines('starship'),
  globe: mountGlobe,
  ...(Object.fromEntries(
    GRAPH_SERIES.map(({ key }): [OverlayWidgetId, OverlayMount] => [
      graphWidgetId(key),
      (container, { stateManager, snapshot }) => {
        new TimeSeriesGraphs(container, stateManager, { chrome: false, series: key, comparisons: snapshot.x ?? [] });
      },
    ]),
  ) as Record<`graph-${(typeof GRAPH_SERIES)[number]['key']}`, OverlayMount>),
};
