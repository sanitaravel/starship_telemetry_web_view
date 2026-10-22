import { formatLatitude, formatLongitude, formatMissionTime, gpsToUnixMs } from './geo';
import { createSwatchButton, toggleColorPopover } from '../../ui/color-picker';
import type { FlightColors } from '../../state/flight-colors';
import type { HistoricalTrajectory } from '../../api/trajectories';
import type { ShipTrack } from '../../core/ship-track';

/** The globe's markup: header with downloads, history bar, canvas with controls, readout. */
export const GLOBE_HTML = `
      <div class="ship-globe__header">
        <h2 class="ship-globe__heading">Ship Position</h2>
        <div class="ship-globe__downloads" role="group" aria-label="Download live tracks" hidden></div>
      </div>
      <div class="ship-globe__history" hidden>
        <span class="ship-globe__history-label">Previous ships:</span>
        <div class="ship-globe__history-toggles"></div>
        <span class="ship-globe__gradient-legend" aria-label="Paths fade from launch to end">
          start <span class="ship-globe__gradient-bar"></span> end
        </span>
      </div>
      <div class="ship-globe__body">
        <div class="ship-globe__map">
          <div class="ship-globe__canvas-wrap">
            <canvas class="ship-globe__canvas" aria-label="Globe showing the ship's position. Drag to rotate, scroll to zoom."></canvas>
            <div class="ship-globe__zoom" role="group" aria-label="Globe view">
              <button type="button" class="ship-globe__zoom-btn ship-globe__center-btn" aria-label="Center on ship" title="Center on ship" disabled>
                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                  <circle cx="12" cy="12" r="7" />
                  <circle cx="12" cy="12" r="2" fill="currentColor" stroke="none" />
                  <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
                </svg>
              </button>
              <button type="button" class="ship-globe__zoom-btn" data-zoom="in" aria-label="Zoom in" title="Zoom in">+</button>
              <button type="button" class="ship-globe__zoom-btn" data-zoom="out" aria-label="Zoom out" title="Zoom out">&minus;</button>
              <button type="button" class="ship-globe__zoom-btn ship-globe__zoom-level" data-zoom="reset" aria-label="Reset zoom" title="Reset zoom">1.0&times;</button>
            </div>
          </div>
          <p class="ship-globe__hint">Drag to rotate &middot; Scroll to zoom</p>
        </div>
        <aside class="ship-globe__panel" aria-label="Active ship">
          <h3 class="ship-globe__panel-title">Active ship</h3>
          <div class="ship-globe__readout" aria-live="polite"></div>
        </aside>
      </div>
    `;

/** The ship with the most recent sample, if any. */
export function latestShip(tracks: ShipTrack[]): ShipTrack | undefined {
  let best: ShipTrack | undefined;
  for (const track of tracks) {
    const last = track.positions[track.positions.length - 1];
    const bestLast = best?.positions[best.positions.length - 1];
    if (last && (!bestLast || last.gpsTime > bestLast.gpsTime)) best = track;
  }
  return best;
}

/** One download button per live track, in the trajectories/ CSV format. */
export function renderDownloads(downloads: HTMLElement, tracks: ShipTrack[]): void {
  const ids = tracks.map((t) => t.id).join(',');
  if (downloads.dataset.tracks === ids) return;
  downloads.dataset.tracks = ids;
  downloads.hidden = tracks.length === 0;
  downloads.innerHTML = tracks
    .map(
      (t) =>
        `<button type="button" class="ship-globe__download-btn" data-track="${t.id}" title="Download the Ship ${t.number} track recorded this session (trajectories/ CSV format)">&#8595; Ship ${t.number} CSV</button>`,
    )
    .join('');
}

/**
 * One on/off toggle per recorded flight, all checked, each with a colour
 * swatch. Returns the swatches by trajectory name.
 */
export function renderHistoryToggles(
  toggles: HTMLElement,
  trajectories: HistoricalTrajectory[],
  colors: FlightColors,
  colorOf: (trajectory: HistoricalTrajectory) => string,
): Map<string, HTMLButtonElement> {
  toggles.innerHTML = trajectories
    .map(
      (t) => `
        <div class="ship-globe__history-item">
          <input type="checkbox" class="ship-globe__history-checkbox" id="ship-globe-history-${t.name}" data-trajectory="${t.name}" checked />
          <label for="ship-globe-history-${t.name}">${t.label}</label>
        </div>`,
    )
    .join('');
  // Swatch buttons go between the checkbox and its label, as in the graphs' compare list.
  const swatches = new Map<string, HTMLButtonElement>();
  for (const t of trajectories) {
    const checkbox = toggles.querySelector(`[data-trajectory="${t.name}"]`)!;
    const swatch = createSwatchButton('ship-globe__history-swatch', t.label, colorOf(t));
    swatch.addEventListener('click', () =>
      toggleColorPopover(swatch, {
        title: t.label,
        color: colorOf(t),
        isCustom: colors.isCustom(t.name),
        onPick: (hex) => colors.set(t.name, hex),
        onReset: () => colors.reset(t.name),
      }),
    );
    checkbox.after(swatch);
    swatches.set(t.name, swatch);
  }
  return swatches;
}

/** The active ship's data card. */
export function renderReadout(readout: HTMLElement, ship: ShipTrack | undefined): void {
  const last = ship?.positions[ship.positions.length - 1];
  if (!ship || !last) {
    readout.innerHTML = `<p class="ship-globe__empty">No ship above ground in the SpaceX tracker feed.</p>`;
    return;
  }
  const fields: [string, string][] = [
    ['Altitude', `${(last.altitude / 1000).toFixed(1)} km`],
    ['Speed', `${Math.round(last.speed * 3.6).toLocaleString('en-US')} km/h`],
    ['Latitude', formatLatitude(last.latitude)],
    ['Longitude', formatLongitude(last.longitude)],
    ['Track points', ship.positions.length.toLocaleString('en-US')],
    ['Updated', new Date(gpsToUnixMs(last.gpsTime)).toISOString().slice(11, 19) + ' UTC'],
  ];
  readout.innerHTML =
    `<div class="ship-globe__vehicle">
        <span class="ship-globe__vehicle-name">Ship ${ship.number}</span>
        <span class="ship-globe__mission-time">${formatMissionTime(last.missionTime)}</span>
      </div>
      <dl class="ship-globe__fields">` +
    fields
      .map(
        ([label, value]) =>
          `<div class="ship-globe__field"><dt class="ship-globe__field-label">${label}</dt><dd class="ship-globe__field-value">${value}</dd></div>`,
      )
      .join('') +
    `</dl>`;
}
