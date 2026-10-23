import { MIN_ZOOM, rotationToCenter, type Rotation } from './geo';
import { downloadText, trackFilename, trackToCsv } from '../../domain/downloads';
import { FlightColors } from '../../state/flight-colors';
import type { HistoricalTrajectory } from '../../api/trajectories';
import type { Box } from './labels';
import type { ShipTrack } from '../../core/ship-track';
import type { ShipTracker } from '../../state/ship-tracker';
import { baseRadius, DEFAULT_ROTATION } from './map-data';
import { GlobeRenderer } from './draw';
import { bindDrag, bindZoom, fitCanvas } from './interaction';
import { GLOBE_HTML, latestShip, renderDownloads, renderHistoryToggles, renderReadout } from './overlay';
import { buildOverlayUrl, type OverlaySnapshot } from '../../domain/overlay/snapshot';
import { overlayWidget } from '../../domain/overlay/widgets';
import { createObsButton, overlayBaseUrl, toggleObsPopover } from '../../ui/obs-popover';

export interface ShipGlobeOptions {
  /**
   * False leaves out the chrome (overlays): heading, OBS button, live track
   * downloads, the previous ships bar, the zoom and centre controls and the
   * hint; the globe always follows the ship and ignores drag and wheel.
   * Default true.
   */
  chrome?: boolean;
  /** Names of the recorded flights hidden whenever the history is set. Default none. */
  hidden?: readonly string[];
  /** Path colour overrides by trajectory name. Default the localStorage-backed store. */
  colors?: FlightColors;
}

/**
 * Rotatable globe showing the live position and track of each ship reported
 * by the SpaceX tracker feed, plus toggleable paths of previously recorded
 * flights. Drag to rotate; "Center on ship" re-focuses.
 */
export class ShipGlobe {
  private readonly canvas: HTMLCanvasElement;
  private readonly readout: HTMLElement;
  private readonly zoomLevel: HTMLElement;
  private readonly controls: HTMLElement;
  private readonly centerButton: HTMLButtonElement;
  private readonly historyBar: HTMLElement;
  private readonly historyToggles: HTMLElement;
  private readonly downloads: HTMLElement;
  private readonly renderer = new GlobeRenderer();
  private rotation: Rotation = DEFAULT_ROTATION;
  private tracks: ShipTrack[] = [];
  private history: HistoricalTrajectory[] = [];
  private hiddenHistory = new Set<string>();
  /** User-picked path colours, keyed by trajectory name; separate from the graphs' colours. */
  private historyColors: FlightColors;
  private historySwatches = new Map<string, HTMLButtonElement>();
  /** Recorded flights hidden whenever the history is set (overlays). */
  private readonly initiallyHidden: readonly string[];
  /** Overlays size the globe to the page rather than to the column width. */
  private readonly fill: boolean;
  private followShip = true;
  private zoom = MIN_ZOOM;
  private width = 0;
  private height = 0;
  private frameRequested = false;

  constructor(container: HTMLElement, tracker: ShipTracker, options: ShipGlobeOptions = {}) {
    const chrome = options.chrome ?? true;
    this.historyColors = options.colors ?? new FlightColors('trajectory');
    this.initiallyHidden = options.hidden ?? [];
    this.fill = !chrome;
    container.classList.add('ship-globe');
    // Fills the page, one frame for map and readout (styles/overlay.css)
    if (!chrome) container.classList.add('ship-globe--overlay');
    container.innerHTML = GLOBE_HTML;
    this.canvas = container.querySelector('.ship-globe__canvas')!;
    this.readout = container.querySelector('.ship-globe__readout')!;
    this.zoomLevel = container.querySelector('.ship-globe__zoom-level')!;
    this.controls = container.querySelector('.ship-globe__zoom')!;
    this.centerButton = container.querySelector('.ship-globe__center-btn')!;
    this.historyBar = container.querySelector('.ship-globe__history')!;
    this.historyToggles = container.querySelector('.ship-globe__history-toggles')!;
    this.downloads = container.querySelector('.ship-globe__downloads')!;
    if (chrome) {
      container.querySelector('.ship-globe__header')!.appendChild(this.createObsButton());
    } else {
      // Removed after being looked up: the class keeps them, detached, and works unchanged
      container
        .querySelectorAll('.ship-globe__header, .ship-globe__history, .ship-globe__zoom, .ship-globe__hint')
        .forEach((el) => el.remove());
    }

    this.centerButton.addEventListener('click', () => {
      this.followShip = true;
      this.centerOnLatest();
      this.requestDraw();
    });
    this.downloads.addEventListener('click', (e) => {
      const id = (e.target as HTMLElement).closest<HTMLElement>('[data-track]')?.dataset.track;
      const track = this.tracks.find((t) => t.id === id);
      if (track) downloadText(trackFilename(track), trackToCsv(track), 'text/csv');
    });
    if (chrome) {
      bindDrag(this.canvas, {
        getRotation: () => this.rotation,
        setRotation: (rotation) => {
          this.rotation = rotation;
        },
        radius: () => baseRadius(this.width, this.height) * this.zoom,
        onGrab: () => {
          this.followShip = false;
        },
        requestDraw: () => this.requestDraw(),
      });
      bindZoom(this.canvas, container, () => this.zoom, (zoom) => this.setZoom(zoom));
    }
    this.historyToggles.addEventListener('change', (e) => {
      const input = e.target as HTMLInputElement;
      const name = input.dataset.trajectory;
      if (!name) return;
      if (input.checked) this.hiddenHistory.delete(name);
      else this.hiddenHistory.add(name);
      this.requestDraw();
    });

    const wrap = container.querySelector<HTMLElement>('.ship-globe__canvas-wrap')!;
    new ResizeObserver(() => this.resize(wrap)).observe(wrap);
    this.resize(wrap);

    // Widths measured with the fallback font are wrong once JetBrains Mono loads.
    void document.fonts?.ready.then(() => {
      this.renderer.clearLabelWidths();
      this.requestDraw();
    });

    this.historyColors.subscribe(() => this.onHistoryColorsChange());
    tracker.subscribe((tracks) => this.update(tracks));
  }

  /** The globe overlay's settings: the path colour overrides and the hidden recorded flights. */
  overlaySnapshot(): OverlaySnapshot {
    return { v: 1, t: this.historyColors.snapshot(), h: [...this.hiddenHistory] };
  }

  /** OBS button offering the globe as an overlay. */
  private createObsButton(): HTMLButtonElement {
    const button = createObsButton('Ship Position');
    button.classList.add('ship-globe__obs');
    button.addEventListener('click', () =>
      toggleObsPopover(button, {
        title: 'Ship Position',
        widgets: [overlayWidget('globe')],
        selected: 'globe',
        urlFor: (id) => buildOverlayUrl(overlayBaseUrl(), id, this.overlaySnapshot()),
      }),
    );
    return button;
  }

  /**
   * Replaces the recorded flight paths and their on/off toggles. All start
   * visible except the `hidden` option's.
   */
  setHistory(trajectories: HistoricalTrajectory[]): void {
    this.history = trajectories;
    this.hiddenHistory = new Set(this.initiallyHidden);
    this.historyBar.hidden = trajectories.length === 0;
    this.historySwatches = renderHistoryToggles(
      this.historyToggles,
      trajectories,
      this.historyColors,
      (t) => this.trajectoryColor(t),
    );
    this.requestDraw();
  }

  /** The user's colour for a recorded flight, else its palette colour. */
  private trajectoryColor(trajectory: HistoricalTrajectory): string {
    return this.historyColors.get(trajectory.name, trajectory.color);
  }

  private onHistoryColorsChange(): void {
    for (const t of this.history) {
      const swatch = this.historySwatches.get(t.name);
      if (swatch) swatch.style.backgroundColor = this.trajectoryColor(t);
    }
    this.requestDraw();
  }

  private update(tracks: ShipTrack[]): void {
    this.tracks = tracks;
    this.centerButton.disabled = !latestShip(this.tracks);
    renderDownloads(this.downloads, this.tracks);
    if (this.followShip) this.centerOnLatest();
    renderReadout(this.readout, latestShip(this.tracks));
    this.requestDraw();
  }

  private centerOnLatest(): void {
    const ship = latestShip(this.tracks);
    const last = ship?.positions[ship.positions.length - 1];
    if (last) this.rotation = rotationToCenter(last.longitude, last.latitude);
  }

  private setZoom(zoom: number): void {
    if (zoom === this.zoom) return;
    this.zoom = zoom;
    this.zoomLevel.textContent = `${zoom.toFixed(1)}×`;
    this.requestDraw();
  }

  private resize(wrap: HTMLElement): void {
    const size = fitCanvas(this.canvas, wrap, { width: this.width, height: this.height }, this.fill);
    if (!size) return;
    this.width = size.width;
    this.height = size.height;
    this.requestDraw();
  }

  private requestDraw(): void {
    if (this.frameRequested) return;
    this.frameRequested = true;
    requestAnimationFrame(() => {
      this.frameRequested = false;
      this.renderer.draw(this.canvas, {
        width: this.width,
        height: this.height,
        rotation: this.rotation,
        zoom: this.zoom,
        tracks: this.tracks,
        history: this.history,
        hiddenHistory: this.hiddenHistory,
        trajectoryColor: (t) => this.trajectoryColor(t),
        controlsBox: () => this.controlsBox(),
      });
    });
  }

  /** Where the on-canvas control buttons sit, in canvas pixels. */
  private controlsBox(): Box {
    const canvas = this.canvas.getBoundingClientRect();
    const controls = this.controls.getBoundingClientRect();
    return {
      x: controls.left - canvas.left,
      y: controls.top - canvas.top,
      w: controls.width,
      h: controls.height,
    };
  }
}
