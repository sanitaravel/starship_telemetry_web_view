import './styles.css';
import { createTelemetryWebSocket, getWebSocketUrl } from './websocket';
import { StateManager } from './state';
import { PipelineControls } from './pipeline-controls';
import { TelemetryDisplay } from './telemetry-display';
import { EngineVisualizer } from './engine-visualizer';
import { TimeSeriesGraphs, resolvePopoutKey, parseCompareParam } from './time-series-graphs';
import { FramePreview } from './frame-preview';

const app = document.getElementById('app');

// OBS overlay mode: `?popout=<seriesKey>` renders a single transparent chart
// that connects to the live telemetry feed on its own. An optional
// `compare=<file1,file2>` param draws the same reference flights as the dashboard.
const params = new URLSearchParams(window.location.search);
const overlayKey = resolvePopoutKey(params.get('popout'));
const compareFilenames = parseCompareParam(params.get('compare'));

if (app && overlayKey) {
  app.innerHTML = '<div id="time-series-graphs"></div>';

  const stateManager = new StateManager();
  const ws = createTelemetryWebSocket();
  ws.onMessage((message) => stateManager.handleMessage(message));
  ws.connect(getWebSocketUrl());

  const graphsContainer = document.getElementById('time-series-graphs');
  if (graphsContainer) {
    new TimeSeriesGraphs(graphsContainer, stateManager, { overlayKey, compareFilenames });
  }
} else if (app) {
  app.innerHTML = `
    <header class="dashboard-header">
      <h1>Starship Telemetry Dashboard</h1>
    </header>
    <section class="dashboard-controls" aria-label="Pipeline Controls">
      <div id="pipeline-controls"></div>
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
    <section class="dashboard-graphs" aria-label="Time-Series Graphs">
      <div id="time-series-graphs"></div>
    </section>
  `;

  // Initialize state manager
  const stateManager = new StateManager();

  // Initialize WebSocket client
  const ws = createTelemetryWebSocket();
  ws.onMessage((message) => stateManager.handleMessage(message));
  ws.connect(getWebSocketUrl());

  // Initialize Pipeline Controls UI
  const controlsContainer = document.getElementById('pipeline-controls');
  if (controlsContainer) {
    new PipelineControls(controlsContainer, ws, stateManager);
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

  // Initialize Time-Series Graphs UI
  const graphsContainer = document.getElementById('time-series-graphs');
  if (graphsContainer) {
    new TimeSeriesGraphs(graphsContainer, stateManager);
  }
}
