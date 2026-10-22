import './styles.css';
import { createTelemetryWebSocket, getWebSocketUrl } from './api/websocket';
import { StateManager } from './state/state-manager';
import { PipelineControls } from './features/pipeline-controls';
import { TelemetryDisplay } from './features/telemetry-display';
import { EngineVisualizer } from './features/engines/visualizer';
import { TimeSeriesGraphs } from './time-series-graphs';
import { FramePreview } from './features/frame-preview';
import { TelemetryDownloads } from './features/telemetry-downloads';
import { ShipGlobe } from './ship-globe';
import { ShipTracker } from './state/ship-tracker';
import { fetchHistoricalTrajectories } from './api/trajectories';
import { appVersion } from './core/app-version';
import { BackendVersion } from './features/header/backend-version';

const app = document.getElementById('app');
if (app) {
  app.innerHTML = `
    <header class="dashboard-header">
      <h1>Starship Telemetry Dashboard</h1>
      <span class="dashboard-header__version" title="Version (release.commit)">v${appVersion()}</span>
      <span class="dashboard-header__version" id="backend-version"></span>
    </header>
    <section class="dashboard-controls" aria-label="Pipeline Controls">
      <div id="pipeline-controls"></div>
      <div id="telemetry-downloads"></div>
    </section>
    <section class="dashboard-main" aria-label="Telemetry and Frame">
      <div class="dashboard-main__telemetry">
        <div id="telemetry-display"></div>
      </div>
      <div class="dashboard-main__frame">
        <div id="frame-preview"></div>
      </div>
    </section>
    <section class="dashboard-engines" aria-label="Engine Status">
      <div id="engine-visualizer"></div>
    </section>
    <section class="dashboard-globe" aria-label="Ship Position">
      <div id="ship-globe"></div>
    </section>
    <section class="dashboard-graphs" aria-label="Time-Series Graphs">
      <div id="time-series-graphs"></div>
    </section>
  `;

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
