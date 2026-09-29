/**
 * Parse a MET string like "+00:01:23" or "-00:00:05" into seconds from T-0.
 */
export function parseMETToSeconds(met: string): number {
  const sign = met.startsWith('-') ? -1 : 1;
  const stripped = met.replace(/^[+\-T]/, '');
  const parts = stripped.split(':');
  const hours = parseInt(parts[0], 10) || 0;
  const minutes = parseInt(parts[1], 10) || 0;
  const seconds = parseInt(parts[2], 10) || 0;
  return sign * (hours * 3600 + minutes * 60 + seconds);
}

/**
 * Format seconds into a MET display string for axis ticks.
 */
export function formatSecondsToMET(totalSeconds: number): string {
  const sign = totalSeconds < 0 ? '-' : '+';
  const abs = Math.abs(Math.round(totalSeconds));
  const h = Math.floor(abs / 3600);
  const m = Math.floor((abs % 3600) / 60);
  const s = abs % 60;
  if (h > 0) {
    return `${sign}${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  return `${sign}${m}:${String(s).padStart(2, '0')}`;
}

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
