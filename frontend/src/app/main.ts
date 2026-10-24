import '../styles/index.css';
import { createTelemetryWebSocket, getWebSocketUrl } from '../api/websocket';
import { StateManager } from '../state/state-manager';
import { TimelineStatusStore } from '../state/timeline-status';
import { MissionTimeline } from '../state/mission-timeline';
import { CurrentMission } from '../state/current-mission';
import { EngineSerials } from '../state/engine-serials';
import type { Vehicle } from '../domain/engines/layout';
import { PipelineControls } from '../features/pipeline-controls';
import { TelemetryDisplay } from '../features/telemetry-display';
import { EngineVisualizer } from '../features/engines/visualizer';
import { TimelinePanel } from '../features/timeline';
import { TimeSeriesGraphs } from '../features/graphs';
import { FramePreview } from '../features/frame-preview';
import { TelemetryDownloads } from '../features/telemetry-downloads';
import { ShipGlobe } from '../features/ship-globe';
import { ShipTracker } from '../state/ship-tracker';
import { fetchHistoricalTrajectories } from '../api/trajectories';
import { BackendVersion } from '../features/header/backend-version';
import { renderLayout } from './layout';
import { mountOverlay } from './overlay';
import { parseOverlayLocation } from '../domain/overlay/snapshot';

const app = document.getElementById('app');
// ?overlay=<id>: one widget for an OBS Browser Source (app/overlay.ts)
const overlayRoute = parseOverlayLocation(window.location.search);
if (app && overlayRoute) {
  mountOverlay(app, overlayRoute);
} else if (app) {
  renderLayout(app);

  // Initialize state manager
  const stateManager = new StateManager();

  // Backend version next to the page's own, flagged when they differ
  const backendVersionEl = document.getElementById('backend-version');
  if (backendVersionEl) {
    new BackendVersion(backendVersionEl, stateManager);
  }

  // The mission timeline's statuses, decided by the backend
  const timelineStatus = new TimelineStatusStore();

  // The mission the engine panels and the timeline share: its serials, names and planned events
  const serials: Record<Vehicle, EngineSerials> = {
    starship: new EngineSerials('starship'),
    superheavy: new EngineSerials('superheavy'),
  };
  const mission = new CurrentMission(serials);
  const timeline = new MissionTimeline(timelineStatus);
  timeline.followMission(mission);

  // Initialize WebSocket client
  const ws = createTelemetryWebSocket();
  ws.onMessage((message) => stateManager.handleMessage(message));
  ws.onMessage((message) => timelineStatus.handleMessage(message));
  ws.connect(getWebSocketUrl());
  void timelineStatus.load();

  // Initialize Pipeline Controls UI
  const controlsContainer = document.getElementById('pipeline-controls');
  if (controlsContainer) {
    new PipelineControls(controlsContainer, ws, stateManager);
  }

  // Downloads of the OCR telemetry received this session
  const downloadsContainer = document.getElementById('telemetry-downloads');
  if (downloadsContainer) {
    new TelemetryDownloads(downloadsContainer, stateManager, () => timelineStatus.snapshot);
  }

  // Initialize Telemetry Display UI
  const telemetryContainer = document.getElementById('telemetry-display');
  if (telemetryContainer) {
    new TelemetryDisplay(telemetryContainer, stateManager);
  }

  // Initialize Engine Visualizer UI
  const engineContainer = document.getElementById('engine-visualizer');
  if (engineContainer) {
    new EngineVisualizer(engineContainer, stateManager, { serials, mission });
  }

  // Mission timeline with the status of each event
  const timelineContainer = document.getElementById('mission-timeline');
  if (timelineContainer) {
    new TimelinePanel(timelineContainer, timeline, stateManager);
  }

  // Initialize Frame Preview UI
  const frameContainer = document.getElementById('frame-preview');
  if (frameContainer) {
    new FramePreview(frameContainer, stateManager);
  }

  // Initialize Ship Globe (polls the public SpaceX tracker feed)
  const globeContainer = document.getElementById('ship-globe');
  if (globeContainer) {
    const tracker = new ShipTracker();
    const globe = new ShipGlobe(globeContainer, tracker);
    tracker.start();
    void fetchHistoricalTrajectories().then((trajectories) => globe.setHistory(trajectories));
  }

  // Initialize Time-Series Graphs UI
  const graphsContainer = document.getElementById('time-series-graphs');
  if (graphsContainer) {
    new TimeSeriesGraphs(graphsContainer, stateManager, { timeline });
  }
}
