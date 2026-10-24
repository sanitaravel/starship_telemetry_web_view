import { appVersion } from '../core/app-version';

/** The dashboard's HTML shell: one mount point per feature, filled in by main.ts. */
export function renderLayout(app: HTMLElement): void {
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
    <section class="dashboard-timeline" aria-label="Mission Timeline">
      <div id="mission-timeline"></div>
    </section>
    <section class="dashboard-globe" aria-label="Ship Position">
      <div id="ship-globe"></div>
    </section>
    <section class="dashboard-graphs" aria-label="Time-Series Graphs">
      <div id="time-series-graphs"></div>
    </section>
  `;
}
