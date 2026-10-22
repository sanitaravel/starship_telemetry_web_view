import '../styles/index.css';
import { createTelemetryWebSocket, getWebSocketUrl } from '../api/websocket';
import { StateManager } from '../state/state-manager';
import { PipelineControls } from '../features/pipeline-controls';
import { TelemetryDisplay } from '../features/telemetry-display';
import { EngineVisualizer } from '../features/engines/visualizer';
import { TimeSeriesGraphs } from '../features/graphs';
import { FramePreview } from '../features/frame-preview';
import { TelemetryDownloads } from '../features/telemetry-downloads';
import { ShipGlobe } from '../features/ship-globe';
import { ShipTracker } from '../state/ship-tracker';
import { fetchHistoricalTrajectories } from '../api/trajectories';
import { BackendVersion } from '../features/header/backend-version';
import { renderLayout } from './layout';

const app = document.getElementById('app');
if (app) {
  renderLayout(app);

  // Initialize state manager
  const stateManager = new StateManager();

  // Backend version next to the page's own, flagged when they differ
  const backendVersionEl = document.getElementById('backend-version');
  if (backendVersionEl) {
    new BackendVersion(backendVersionEl, stateManager);
  }

  // Initialize WebSocket client
  const ws = createTelemetryWebSocket();
  ws.onMessage((message) => stateManager.handleMessage(message));
  ws.connect(getWebSocketUrl());

  // Initialize Pipeline Controls UI
  const controlsContainer = document.getElementById('pipeline-controls');
  if (controlsContainer) {
    new PipelineControls(controlsContainer, ws, stateManager);
  }

  // Downloads of the OCR telemetry received this session
  const downloadsContainer = document.getElementById('telemetry-downloads');
  if (downloadsContainer) {
    new TelemetryDownloads(downloadsContainer, stateManager);
  }

  // Initialize Telemetry Display UI
  const telemetryContainer = document.getElementById('telemetry-display');
  if (telemetryContainer) {
    new TelemetryDisplay(telemetryContainer, stateManager);
  }

  // Initialize Engine Visualizer UI
  const engineContainer = document.getElementById('engine-visualizer');
  if (engineContainer) {
    new EngineVisualizer(engineContainer, stateManager);
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
    new TimeSeriesGraphs(graphsContainer, stateManager);
  }
}
