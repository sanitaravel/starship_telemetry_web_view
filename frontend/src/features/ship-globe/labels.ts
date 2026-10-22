import { geoArea, geoCentroid, geoDistance } from 'd3-geo';
import type { Feature, Geometry, MultiPolygon, Polygon } from 'geojson';

/** A country's label: where to anchor it and how big the country is. */
export interface CountryLabel {
  name: string;
  /** [longitude, latitude] at the centre of the country's largest landmass. */
  anchor: [number, number];
  /** Area of the whole country in steradians (the unit sphere has 4π). */
  area: number;
}

/** A label that passed the size and collision checks, in canvas pixels. */
export interface PlacedLabel {
  name: string;
  x: number;
  y: number;
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * A country is labelled once its on-screen size reaches this multiple of the
 * label's width, so names appear as the user zooms in.
 */
const MIN_SIZE_TO_LABEL_RATIO = 0.9;

/** Labels further than this from the view centre are skipped (too foreshortened). */
const MAX_LABEL_DISTANCE = (75 * Math.PI) / 180;

/** Space kept clear around each label, in pixels. */
const LABEL_PADDING = 4;

/**
 * Builds one label per named country, largest first. Multi-part countries
 * are anchored on their largest part, so e.g. the USA label is not pulled
 * toward Alaska.
 */
export function countryLabels(features: Feature<Geometry | null, { name?: string }>[]): CountryLabel[] {
  const labels: CountryLabel[] = [];
  for (const f of features) {
    const name = f.properties?.name;
    const geometry = f.geometry;
    if (!name || !geometry || (geometry.type !== 'Polygon' && geometry.type !== 'MultiPolygon')) continue;
    labels.push({ name, anchor: largestPartCentroid(geometry), area: geoArea(f) });
  }
  return labels.sort((a, b) => b.area - a.area);
}

function largestPartCentroid(geometry: Polygon | MultiPolygon): [number, number] {
  if (geometry.type === 'Polygon') return geoCentroid(geometry) as [number, number];
  let best: Polygon | null = null;
  let bestArea = -1;
  for (const coordinates of geometry.coordinates) {
    const part: Polygon = { type: 'Polygon', coordinates };
    const area = geoArea(part);
    if (area > bestArea) {
      bestArea = area;
      best = part;
    }
  }
  return geoCentroid(best!) as [number, number];
}

/**
 * Approximate on-screen size of a country in pixels: the side of a square
 * with its projected area, shrunk toward the globe's edge where the
 * orthographic projection foreshortens it.
 */
export function apparentSize(area: number, scale: number, distanceFromCenter: number): number {
  return Math.sqrt(area) * scale * Math.cos(distanceFromCenter);
}

export function overlaps(a: Box, b: Box): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * Chooses which labels to draw for the current view. Labels are considered
 * largest country first; one is kept if its country is on the visible side,
 * big enough on screen for the text, and clear of every label already kept.
 *
 * @param center  [longitude, latitude] at the centre of the view
 * @param scale   projection scale (globe radius in pixels, including zoom)
 * @param project maps [longitude, latitude] to canvas pixels
 * @param measure label width in pixels
 * @param height  label height in pixels
 * @param reserved areas labels must stay clear of (e.g. on-canvas controls)
 */
export function placeLabels(
  labels: CountryLabel[],
  center: [number, number],
  scale: number,
  project: (point: [number, number]) => [number, number] | null,
  measure: (name: string) => number,
  height: number,
  viewport: { width: number; height: number },
  reserved: Box[] = [],
): PlacedLabel[] {
  const placed: PlacedLabel[] = [];
  const boxes: Box[] = [...reserved];
  for (const label of labels) {
    const distance = geoDistance(label.anchor, center);
    if (distance > MAX_LABEL_DISTANCE) continue;
    const width = measure(label.name);
    if (apparentSize(label.area, scale, distance) < width * MIN_SIZE_TO_LABEL_RATIO) continue;
    const point = project(label.anchor);
    if (!point) continue;
    const [x, y] = point;
    const box: Box = {
      x: x - width / 2 - LABEL_PADDING,
      y: y - height / 2 - LABEL_PADDING,
      w: width + LABEL_PADDING * 2,
      h: height + LABEL_PADDING * 2,
    };
    if (box.x < 0 || box.y < 0 || box.x + box.w > viewport.width || box.y + box.h > viewport.height) continue;
    if (boxes.some((b) => overlaps(b, box))) continue;
    boxes.push(box);
    placed.push({ name: label.name, x, y });
  }
  return placed;
}
