import { geoDistance } from 'd3-geo';

/** Rotation of an orthographic projection as [lambda, phi] in degrees. */
export type Rotation = [number, number];

/** Degrees of rotation per pixel of drag at a globe radius of 1px. */
const DRAG_SENSITIVITY = 57.2958; // 180 / π: dragging one radius turns ~1 radian

/** Rotation that puts the given point at the centre of the globe. */
export function rotationToCenter(longitude: number, latitude: number): Rotation {
  return [-longitude, -latitude];
}

/**
 * Applies a pointer drag of (dx, dy) pixels to a rotation. Latitude is clamped
 * so the globe cannot flip over the poles.
 */
export function applyDrag(rotation: Rotation, dx: number, dy: number, radius: number): Rotation {
  const k = DRAG_SENSITIVITY / Math.max(radius, 1);
  const lambda = normalizeLongitude(rotation[0] + dx * k);
  const phi = Math.max(-90, Math.min(90, rotation[1] - dy * k));
  return [lambda, phi];
}

export function normalizeLongitude(lon: number): number {
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

/** True when the point faces the viewer for the given rotation. */
export function isOnVisibleHemisphere(
  longitude: number,
  latitude: number,
  rotation: Rotation,
): boolean {
  const center: [number, number] = [-rotation[0], -rotation[1]];
  return geoDistance([longitude, latitude], center) < Math.PI / 2;
}

export function formatLatitude(lat: number): string {
  return `${Math.abs(lat).toFixed(3)}° ${lat >= 0 ? 'N' : 'S'}`;
}

export function formatLongitude(lon: number): string {
  return `${Math.abs(lon).toFixed(3)}° ${lon >= 0 ? 'E' : 'W'}`;
}

/** GPS epoch (1980-01-06) in Unix ms, minus the current 18 s GPS–UTC leap offset. */
const GPS_EPOCH_UNIX_MS = 315_964_800_000;
const GPS_UTC_LEAP_SECONDS = 18;

export function gpsToUnixMs(gpsSeconds: number): number {
  return GPS_EPOCH_UNIX_MS + (gpsSeconds - GPS_UTC_LEAP_SECONDS) * 1000;
}

/** A slice of a path with its position along it: 0 at the start, 1 at the end. */
export interface GradientSegment {
  coordinates: [number, number][];
  t: number;
}

/**
 * Splits a path into at most `count` consecutive slices so it can be stroked
 * as a start-to-end gradient. Neighbouring slices share their boundary point,
 * so the stroked line has no gaps.
 */
export function gradientSegments(
  coordinates: [number, number][],
  count: number,
): GradientSegment[] {
  const edges = coordinates.length - 1;
  if (edges < 1) return [];
  const slices = Math.max(1, Math.min(count, edges));
  const segments: GradientSegment[] = [];
  for (let i = 0; i < slices; i++) {
    const from = Math.floor((i * edges) / slices);
    const to = Math.floor(((i + 1) * edges) / slices);
    segments.push({
      coordinates: coordinates.slice(from, to + 1),
      t: slices === 1 ? 1 : i / (slices - 1),
    });
  }
  return segments;
}

/** `#rrggbb` → `rgba(r, g, b, alpha)`. */
export function hexToRgba(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.replace('#', ''), 16);
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}
