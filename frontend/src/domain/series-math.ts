import type { TimeSeriesPoint } from '../core/time-series';
import { pointSeconds } from '../core/met';

export const GAP_THRESHOLD_SECONDS = 5;

/**
 * Sort points by x-value, deduplicate (first value per x wins), and insert
 * NaN gap markers where consecutive points are more than GAP_THRESHOLD_SECONDS apart.
 * This causes Chart.js to break the line at data gaps.
 */
export function deduplicateByX(points: { x: number; y: number }[]): { x: number; y: number | null }[] {
  if (points.length === 0) return [];

  // Use a Map to keep the first y value for each x
  const map = new Map<number, number>();
  for (const p of points) {
    if (!map.has(p.x)) {
      map.set(p.x, p.y);
    }
  }

  // Convert back to sorted array
  const sorted: { x: number; y: number }[] = [];
  for (const [x, y] of map) {
    sorted.push({ x, y });
  }
  sorted.sort((a, b) => a.x - b.x);

  // Insert NaN gap markers between points that are too far apart
  const result: { x: number; y: number | null }[] = [];
  for (let i = 0; i < sorted.length; i++) {
    if (i > 0 && sorted[i].x - sorted[i - 1].x > GAP_THRESHOLD_SECONDS) {
      // Insert a gap point midway to break the line
      result.push({ x: (sorted[i - 1].x + sorted[i].x) / 2, y: null });
    }
    result.push(sorted[i]);
  }
  return result;
}

/**
 * Maintains deduplicateByX(points → {x: pointSeconds, y: value}) for a series
 * that only grows, without redoing the whole series on every update.
 *
 * Points that arrive in time order are appended (with a gap marker where
 * needed) in O(1) each; the first out-of-order point, a different source
 * array or a shrunk one triggers a full rebuild with deduplicateByX. The
 * returned array is reused between updates while appending, so it can be
 * handed to Chart.js as-is.
 */
export class IncrementalXY {
  private source: TimeSeriesPoint[] | null = null;
  private consumed = 0;
  private seen = new Set<number>();
  private lastX = -Infinity;
  private data: { x: number; y: number | null }[] = [];

  update(points: TimeSeriesPoint[]): { x: number; y: number | null }[] {
    if (points !== this.source || points.length < this.consumed) {
      return this.rebuild(points);
    }
    for (let i = this.consumed; i < points.length; i++) {
      const x = pointSeconds(points[i]);
      if (this.seen.has(x)) continue;
      if (x < this.lastX) {
        return this.rebuild(points);
      }
      if (this.data.length > 0 && x - this.lastX > GAP_THRESHOLD_SECONDS) {
        this.data.push({ x: (this.lastX + x) / 2, y: null });
      }
      this.data.push({ x, y: points[i].value });
      this.seen.add(x);
      this.lastX = x;
    }
    this.consumed = points.length;
    return this.data;
  }

  private rebuild(points: TimeSeriesPoint[]): { x: number; y: number | null }[] {
    this.source = points;
    this.consumed = points.length;
    this.data = deduplicateByX(points.map((p) => ({ x: pointSeconds(p), y: p.value })));
    this.seen = new Set(points.map(pointSeconds));
    this.lastX = this.data.length > 0 ? this.data[this.data.length - 1].x : -Infinity;
    return this.data;
  }
}

/** Standard gravitational acceleration in m/s². */
export const STANDARD_GRAVITY = 9.80665;
/** Conversion factor from km/h to m/s. */
export const KMH_TO_MS = 1000 / 3600;
/**
 * Maximum time gap (seconds) between two speed samples for which a derivative
 * is still meaningful. Larger gaps produce a break in the acceleration line.
 */
export const ACCELERATION_MAX_DT_SECONDS = 10;
/**
 * Minimum time between the two speed samples a derivative is taken over.
 * Samples arrive several times per second, and differencing neighbours that
 * close amplifies OCR noise, so each sample is compared with the latest one
 * at least this far back.
 */
export const ACCELERATION_MIN_DT_SECONDS = 1;

/**
 * Derive an acceleration series (in g) from a speed series.
 *
 * Speed is assumed to be in km/h (matching the telemetry stream). Acceleration
 * is the time-derivative of speed expressed in units of standard gravity:
 * a = (dv/dt) / 9.80665.
 *
 * Each speed sample is differenced against the latest earlier sample at least
 * ACCELERATION_MIN_DT_SECONDS before it (a backward finite difference), and
 * the result is plotted at the midpoint of that interval. Samples with no such
 * earlier sample are skipped. Where the interval is longer than
 * ACCELERATION_MAX_DT_SECONDS, a null gap marker is emitted instead so Chart.js
 * breaks the line rather than drawing across the gap (matching the other
 * series, which use deduplicateByX for the same effect).
 */
export function computeAcceleration(points: TimeSeriesPoint[]): { x: number; y: number | null }[] {
  if (points.length < 2) return [];

  // Sort/deduplicate by time so the derivative is monotonic in time.
  const map = new Map<number, number>();
  for (const p of points) {
    const t = pointSeconds(p);
    if (!map.has(t)) {
      map.set(t, p.value);
    }
  }
  const sorted = Array.from(map.entries())
    .map(([x, v]) => ({ x, v }))
    .sort((a, b) => a.x - b.x);

  const result: { x: number; y: number | null }[] = [];
  // Index of the latest sample at least ACCELERATION_MIN_DT_SECONDS before i.
  let j = 0;
  for (let i = 1; i < sorted.length; i++) {
    while (j + 1 < i && sorted[i].x - sorted[j + 1].x >= ACCELERATION_MIN_DT_SECONDS) {
      j++;
    }
    const dt = sorted[i].x - sorted[j].x;
    if (dt < ACCELERATION_MIN_DT_SECONDS) continue;
    const midX = (sorted[i].x + sorted[j].x) / 2;

    if (dt > ACCELERATION_MAX_DT_SECONDS) {
      // Break the line across the gap instead of interpolating over it.
      result.push({ x: midX, y: null });
      continue;
    }

    const dvMs = (sorted[i].v - sorted[j].v) * KMH_TO_MS;
    // Plot at the midpoint of the interval the derivative represents.
    result.push({ x: midX, y: dvMs / dt / STANDARD_GRAVITY });
  }
  return result;
}
