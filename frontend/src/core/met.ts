import type { TimeSeriesPoint } from './time-series';

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
 * Time of a point in seconds from T-0: the sub-second estimate when the point
 * has one, otherwise the whole-second MET string.
 */
export function pointSeconds(p: TimeSeriesPoint): number {
  return p.metSeconds ?? parseMETToSeconds(p.missionElapsedTime);
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
