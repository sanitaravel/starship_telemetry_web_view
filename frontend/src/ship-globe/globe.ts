import { geoGraticule10, geoOrthographic, geoPath } from 'd3-geo';
import type { GeoPermissibleObjects } from 'd3-geo';
import { feature } from 'topojson-client';
import type { Topology, GeometryCollection } from 'topojson-specification';
import landTopology from 'world-atlas/land-110m.json';
import {
  applyDrag,
  formatLatitude,
  formatLongitude,
  gpsToUnixMs,
  isOnVisibleHemisphere,
  rotationToCenter,
  type Rotation,
} from './geo';
import type { ShipTrack, ShipTracker } from './tracker';

const COLORS = {
  ocean: '#1c1c1c',
  land: '#3d3d3d',
  graticule: 'rgba(254, 254, 254, 0.07)',
  rim: '#444444',
  track: 'rgba(255, 128, 20, 0.6)',
  ship: '#FF8014',
  shipHalo: 'rgba(255, 128, 20, 0.25)',
  label: '#FEFEFE',
};

const topology = landTopology as unknown as Topology<{ land: GeometryCollection }>;
const LAND = feature(topology, topology.objects.land);
const GRATICULE = geoGraticule10();

/** Default view before any ship is known: Starbase, Texas. */
const DEFAULT_ROTATION: Rotation = rotationToCenter(-97.157, 25.997);

/**
 * Rotatable globe showing the live position and track of each ship reported
 * by the SpaceX tracker feed. Drag to rotate; "Center on ship" re-focuses.
 */
export class ShipGlobe {
  private readonly canvas: HTMLCanvasElement;
  private readonly readout: HTMLElement;
  private readonly centerButton: HTMLButtonElement;
  private readonly projection = geoOrthographic().clipAngle(90).precision(0.5);
  private rotation: Rotation = DEFAULT_ROTATION;
  private tracks: ShipTrack[] = [];
  private followShip = true;
  private dragOrigin: { x: number; y: number; rotation: Rotation } | null = null;
  private size = 0;
  private frameRequested = false;

  constructor(container: HTMLElement, tracker: ShipTracker) {
    container.classList.add('ship-globe');
    container.innerHTML = `
      <div class="ship-globe__header">
        <h2 class="ship-globe__heading">Ship Position</h2>
        <button type="button" class="ship-globe__center-btn" disabled>Center on ship</button>
      </div>
      <div class="ship-globe__body">
        <div class="ship-globe__canvas-wrap">
          <canvas class="ship-globe__canvas" aria-label="Globe showing the ship's position. Drag to rotate."></canvas>
        </div>
        <dl class="ship-globe__readout" aria-live="polite"></dl>
      </div>
    `;
    this.canvas = container.querySelector('.ship-globe__canvas')!;
    this.readout = container.querySelector('.ship-globe__readout')!;
    this.centerButton = container.querySelector('.ship-globe__center-btn')!;

    this.centerButton.addEventListener('click', () => {
      this.followShip = true;
      this.centerOnLatest();
      this.requestDraw();
    });
    this.bindDrag();

    const wrap = container.querySelector<HTMLElement>('.ship-globe__canvas-wrap')!;
    new ResizeObserver(() => this.resize(wrap)).observe(wrap);
    this.resize(wrap);

    tracker.subscribe((tracks) => this.update(tracks));
  }

  private update(tracks: ShipTrack[]): void {
    this.tracks = tracks;
    this.centerButton.disabled = !this.latestShip();
    if (this.followShip) this.centerOnLatest();
    this.renderReadout();
    this.requestDraw();
  }

  /** The ship with the most recent sample, if any. */
  private latestShip(): ShipTrack | undefined {
    let best: ShipTrack | undefined;
    for (const track of this.tracks) {
      const last = track.positions[track.positions.length - 1];
      const bestLast = best?.positions[best.positions.length - 1];
      if (last && (!bestLast || last.gpsTime > bestLast.gpsTime)) best = track;
    }
    return best;
  }

  private centerOnLatest(): void {
    const ship = this.latestShip();
    const last = ship?.positions[ship.positions.length - 1];
    if (last) this.rotation = rotationToCenter(last.longitude, last.latitude);
  }

  private bindDrag(): void {
    this.canvas.addEventListener('pointerdown', (e) => {
      this.canvas.setPointerCapture(e.pointerId);
      this.dragOrigin = { x: e.clientX, y: e.clientY, rotation: this.rotation };
      this.followShip = false;
      this.canvas.classList.add('ship-globe__canvas--dragging');
    });
    this.canvas.addEventListener('pointermove', (e) => {
      if (!this.dragOrigin) return;
      const { x, y, rotation } = this.dragOrigin;
      this.rotation = applyDrag(rotation, e.clientX - x, e.clientY - y, this.size / 2);
      this.requestDraw();
    });
    const end = (e: PointerEvent) => {
      if (!this.dragOrigin) return;
      this.dragOrigin = null;
      if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);
      this.canvas.classList.remove('ship-globe__canvas--dragging');
    };
    this.canvas.addEventListener('pointerup', end);
    this.canvas.addEventListener('pointercancel', end);
  }

  private resize(wrap: HTMLElement): void {
    const size = Math.floor(Math.min(wrap.clientWidth, 420));
    if (size <= 0 || size === this.size) return;
    this.size = size;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = size * dpr;
    this.canvas.height = size * dpr;
    this.canvas.style.width = `${size}px`;
    this.canvas.style.height = `${size}px`;
    this.requestDraw();
  }

  private requestDraw(): void {
    if (this.frameRequested) return;
    this.frameRequested = true;
    requestAnimationFrame(() => {
      this.frameRequested = false;
      this.draw();
    });
  }

  private draw(): void {
    const ctx = this.canvas.getContext('2d');
    if (!ctx || this.size === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const radius = this.size / 2 - 2;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, this.size, this.size);

    this.projection
      .scale(radius)
      .translate([this.size / 2, this.size / 2])
      .rotate(this.rotation);
    const path = geoPath(this.projection, ctx);
    const stroke = (obj: GeoPermissibleObjects, color: string, width: number) => {
      ctx.beginPath();
      path(obj);
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.stroke();
    };

    ctx.beginPath();
    path({ type: 'Sphere' });
    ctx.fillStyle = COLORS.ocean;
    ctx.fill();

    stroke(GRATICULE, COLORS.graticule, 1);

    ctx.beginPath();
    path(LAND);
    ctx.fillStyle = COLORS.land;
    ctx.fill();

    for (const track of this.tracks) {
      if (track.positions.length >= 2) {
        stroke(
          {
            type: 'LineString',
            coordinates: track.positions.map((p) => [p.longitude, p.latitude]),
          },
          COLORS.track,
          2,
        );
      }
      this.drawShipMarker(ctx, track);
    }

    stroke({ type: 'Sphere' }, COLORS.rim, 1);
  }

  private drawShipMarker(ctx: CanvasRenderingContext2D, track: ShipTrack): void {
    const last = track.positions[track.positions.length - 1];
    if (!last || !isOnVisibleHemisphere(last.longitude, last.latitude, this.rotation)) return;
    const point = this.projection([last.longitude, last.latitude]);
    if (!point) return;
    const [x, y] = point;

    ctx.beginPath();
    ctx.arc(x, y, 9, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.shipHalo;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.ship;
    ctx.fill();

    ctx.font = '500 11px "JetBrains Mono", monospace';
    ctx.fillStyle = COLORS.label;
    ctx.textBaseline = 'middle';
    ctx.fillText(`S${track.number}`, x + 12, y);
  }

  private renderReadout(): void {
    const ship = this.latestShip();
    const last = ship?.positions[ship.positions.length - 1];
    if (!ship || !last) {
      this.readout.innerHTML = `<p class="ship-globe__empty">No ship above ground in the SpaceX tracker feed.</p>`;
      return;
    }
    const fields: [string, string][] = [
      ['Vehicle', `Ship ${ship.number}`],
      ['Latitude', formatLatitude(last.latitude)],
      ['Longitude', formatLongitude(last.longitude)],
      ['Altitude', `${(last.altitude / 1000).toFixed(1)} km`],
      ['Speed', `${Math.round(last.speed * 3.6).toLocaleString('en-US')} km/h`],
      ['Updated', new Date(gpsToUnixMs(last.gpsTime)).toISOString().slice(11, 19) + ' UTC'],
    ];
    this.readout.innerHTML = fields
      .map(
        ([label, value]) =>
          `<div class="ship-globe__field"><dt class="ship-globe__field-label">${label}</dt><dd class="ship-globe__field-value">${value}</dd></div>`,
      )
      .join('');
  }
}
