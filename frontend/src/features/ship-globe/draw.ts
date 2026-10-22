import { geoOrthographic, geoPath } from 'd3-geo';
import type { GeoPermissibleObjects } from 'd3-geo';
import { gradientSegments, hexToRgba, isOnVisibleHemisphere, type Rotation } from './geo';
import { placeLabels, type Box } from './labels';
import type { HistoricalTrajectory } from '../../api/trajectories';
import type { ShipTrack } from '../../core/ship-track';
import {
  baseRadius,
  BORDERS,
  COLORS,
  COUNTRY_LABELS,
  GRADIENT_ALPHA,
  GRADIENT_STEPS,
  GRADIENT_WIDTH,
  GRATICULE,
  LABEL_FONT,
  LABEL_HEIGHT,
  LAND,
} from './map-data';

/** What one frame of the globe shows. */
export interface GlobeView {
  width: number;
  height: number;
  rotation: Rotation;
  zoom: number;
  tracks: ShipTrack[];
  history: HistoricalTrajectory[];
  hiddenHistory: ReadonlySet<string>;
  trajectoryColor(trajectory: HistoricalTrajectory): string;
  /** Where the on-canvas control buttons sit, in canvas pixels. */
  controlsBox(): Box;
}

/** Draws the globe onto its canvas; owns the projection and the label width cache. */
export class GlobeRenderer {
  private readonly projection = geoOrthographic().clipAngle(90).precision(0.5);
  /** Label widths in pixels for LABEL_FONT; cleared once web fonts load. */
  private labelWidths = new Map<string, number>();

  /** Forget measured label widths (they were taken with the fallback font). */
  clearLabelWidths(): void {
    this.labelWidths.clear();
  }

  draw(canvas: HTMLCanvasElement, view: GlobeView): void {
    const ctx = canvas.getContext('2d');
    if (!ctx || view.width === 0) return;
    const dpr = window.devicePixelRatio || 1;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, view.width, view.height);

    this.projection
      .scale(baseRadius(view.width, view.height) * view.zoom)
      .translate([view.width / 2, view.height / 2])
      .rotate(view.rotation);
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

    stroke(BORDERS, COLORS.border, 0.75);
    this.drawCountryLabels(ctx, view);

    // Paths fade in from launch to their latest point so direction is visible.
    const strokeGradient = (coordinates: [number, number][], color: string) => {
      ctx.lineCap = 'round';
      for (const { coordinates: slice, t } of gradientSegments(coordinates, GRADIENT_STEPS)) {
        const alpha = GRADIENT_ALPHA[0] + (GRADIENT_ALPHA[1] - GRADIENT_ALPHA[0]) * t;
        const width = GRADIENT_WIDTH[0] + (GRADIENT_WIDTH[1] - GRADIENT_WIDTH[0]) * t;
        stroke({ type: 'LineString', coordinates: slice }, hexToRgba(color, alpha), width);
      }
      ctx.lineCap = 'butt';
    };

    for (const trajectory of view.history) {
      if (view.hiddenHistory.has(trajectory.name)) continue;
      const color = view.trajectoryColor(trajectory);
      strokeGradient(trajectory.coordinates, color);
      const [lon, lat] = trajectory.coordinates[trajectory.coordinates.length - 1];
      this.drawPointLabel(ctx, view, lon, lat, 3.5, color, `S${trajectory.number}`);
    }

    for (const track of view.tracks) {
      strokeGradient(
        track.positions.map((p): [number, number] => [p.longitude, p.latitude]),
        COLORS.ship,
      );
      this.drawShipMarker(ctx, view, track);
    }

    stroke({ type: 'Sphere' }, COLORS.rim, 1);
  }

  private drawShipMarker(ctx: CanvasRenderingContext2D, view: GlobeView, track: ShipTrack): void {
    const last = track.positions[track.positions.length - 1];
    if (!last || !isOnVisibleHemisphere(last.longitude, last.latitude, view.rotation)) return;
    const point = this.projection([last.longitude, last.latitude]);
    if (!point) return;
    const [x, y] = point;

    ctx.beginPath();
    ctx.arc(x, y, 12, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.shipHalo;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fillStyle = COLORS.ship;
    ctx.fill();

    ctx.font = '500 13px "JetBrains Mono", monospace';
    ctx.fillStyle = COLORS.label;
    ctx.textBaseline = 'middle';
    ctx.fillText(`S${track.number}`, x + 16, y);
  }

  /** Names of countries that are large enough on screen at the current zoom. */
  private drawCountryLabels(ctx: CanvasRenderingContext2D, view: GlobeView): void {
    ctx.font = LABEL_FONT;
    const center: [number, number] = [-view.rotation[0], -view.rotation[1]];
    const placed = placeLabels(
      COUNTRY_LABELS,
      center,
      this.projection.scale(),
      (point) => this.projection(point),
      (name) => this.measureLabel(ctx, name),
      LABEL_HEIGHT,
      { width: view.width, height: view.height },
      [view.controlsBox()],
    );

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    ctx.lineWidth = 3;
    ctx.strokeStyle = COLORS.countryLabelHalo;
    ctx.fillStyle = COLORS.countryLabel;
    for (const { name, x, y } of placed) {
      ctx.strokeText(name, x, y);
      ctx.fillText(name, x, y);
    }
    ctx.textAlign = 'start';
  }

  private measureLabel(ctx: CanvasRenderingContext2D, name: string): number {
    let width = this.labelWidths.get(name);
    if (width === undefined) {
      width = ctx.measureText(name).width;
      this.labelWidths.set(name, width);
    }
    return width;
  }

  /** Small dot with a label, used for where a recorded flight ended. */
  private drawPointLabel(
    ctx: CanvasRenderingContext2D,
    view: GlobeView,
    longitude: number,
    latitude: number,
    radius: number,
    color: string,
    label: string,
  ): void {
    if (!isOnVisibleHemisphere(longitude, latitude, view.rotation)) return;
    const point = this.projection([longitude, latitude]);
    if (!point) return;
    const [x, y] = point;

    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();

    ctx.font = '500 11px "JetBrains Mono", monospace';
    ctx.fillStyle = color;
    ctx.textBaseline = 'middle';
    ctx.fillText(label, x + radius + 5, y);
  }
}
