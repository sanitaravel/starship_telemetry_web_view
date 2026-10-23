import { createLogger } from '../../core/logger';
import { normaliseSerial } from '../engines/serials';
import type { OverlayWidgetId } from './widgets';

const logger = createLogger('overlay');

/**
 * The dashboard settings an overlay URL carries (`&s=`), as base64url JSON.
 * Keys are short and each widget's snapshot holds only what it uses, so
 * the URL stays well under 2 000 characters (see the caps below).
 *
 * A valid snapshot (one that decodes back to itself) keeps to the caps,
 * has `#rrggbb` colours, unique flight filenames and trajectory names made
 * of [A-Za-z0-9_.-], names without control characters, and serials that
 * normaliseSerial leaves unchanged.
 */
export const SNAPSHOT_VERSION = 1;

export interface OverlaySnapshot {
  v: 1;
  /** graphs: compared flights in load order, colours resolved: [filename, '#rrggbb'] */
  x?: [string, string][];
  /** globe: trajectory colour overrides by name */
  t?: Record<string, string>;
  /** globe: hidden trajectory names */
  h?: string[];
  /** engines: mission, ship and booster names */
  m?: { n: string; ss: string; sh: string };
  /** engines: this vehicle's serials, keyed by engine number ("6" for ss_e6 / sh_e6) */
  e?: Record<string, string>;
}

/** Longest mission name, as the settings panel's input allows. */
export const MAX_MISSION_NAME = 80;
/** Longest ship or booster name, as the settings panel's input allows. */
export const MAX_VEHICLE_NAME = 40;
/** Most compared flights carried. */
export const MAX_COMPARED_FLIGHTS = 6;
/** Longest compared flight filename, e.g. "ift-12.json". */
export const MAX_FLIGHT_FILENAME = 32;
/** Most globe trajectories with a colour override, and most hidden ones. */
export const MAX_TRAJECTORIES = 24;
/** Longest globe trajectory name, e.g. "ship39". */
export const MAX_TRAJECTORY_NAME = 16;
/** Highest engine number (Super Heavy has 33). */
export const MAX_ENGINE_NUMBER = 33;

const HEX_COLOR = /^#[0-9a-f]{6}$/i;
/** Flight filenames and trajectory names go into GET paths, so no slashes or spaces. */
const SAFE_NAME = /^[A-Za-z0-9_.-]+$/;
const BASE64URL = /^[A-Za-z0-9_-]*$/;
/** C0 controls and DEL, which JSON would escape to six characters each. */
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/g;

/** UTF-8 JSON of the snapshot as base64url without padding. */
export function encodeSnapshot(snapshot: OverlaySnapshot): string {
  const bytes = new TextEncoder().encode(JSON.stringify(snapshot));
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * The snapshot in `text`, keeping only the fields that validate. Never
 * throws: a missing, malformed or unknown-version snapshot gives the
 * defaults (`{ v: 1 }`) and logs a warning.
 */
export function decodeSnapshot(text: string | null): OverlaySnapshot {
  if (text === null || text === '') {
    logger.warn('Overlay settings missing, using defaults');
    return { v: SNAPSHOT_VERSION };
  }
  let parsed: unknown;
  try {
    if (!BASE64URL.test(text)) throw new Error('not base64url');
    const binary = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch (err) {
    logger.warn('Overlay settings unreadable, using defaults', { error: String(err) });
    return { v: SNAPSHOT_VERSION };
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    logger.warn('Overlay settings are not an object, using defaults');
    return { v: SNAPSHOT_VERSION };
  }
  const raw = parsed as Record<string, unknown>;
  if (raw.v !== SNAPSHOT_VERSION) {
    logger.warn('Overlay settings have an unknown version, using defaults', { version: String(raw.v) });
    return { v: SNAPSHOT_VERSION };
  }
  return validateSnapshot(raw);
}

/**
 * The snapshot with every field cleaned and capped as decoding does; also
 * used by the dashboard before encoding, so what it sends is what an
 * overlay accepts.
 */
export function validateSnapshot(raw: Record<string, unknown>): OverlaySnapshot {
  const snapshot: OverlaySnapshot = { v: SNAPSHOT_VERSION };
  const x = comparedFlights(raw.x);
  if (x) snapshot.x = x;
  const t = trajectoryColors(raw.t);
  if (t) snapshot.t = t;
  const h = hiddenTrajectories(raw.h);
  if (h) snapshot.h = h;
  const m = missionNames(raw.m);
  if (m) snapshot.m = m;
  const e = engineSerials(raw.e);
  if (e) snapshot.e = e;
  return snapshot;
}

/** `<base>?overlay=<widget>&s=<snapshot>`; `base` is the dashboard's origin and path. */
export function buildOverlayUrl(base: string, widget: OverlayWidgetId, snapshot: OverlaySnapshot): string {
  const s = encodeSnapshot(validateSnapshot(snapshot as unknown as Record<string, unknown>));
  // Both values are URL-safe as they are: widget ids are [a-zA-Z-], s is base64url
  return `${base}?overlay=${widget}&s=${s}`;
}

/**
 * The overlay a page location asks for, or null for the dashboard (no
 * `overlay` parameter). The widget id is returned as given, for the router
 * to reject an unknown one.
 */
export function parseOverlayLocation(search: string): { widget: string; snapshot: OverlaySnapshot } | null {
  const params = new URLSearchParams(search);
  const widget = params.get('overlay');
  if (widget === null) return null;
  return { widget, snapshot: decodeSnapshot(params.get('s')) };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isSafeName(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length <= max && SAFE_NAME.test(value);
}

function comparedFlights(value: unknown): [string, string][] | null {
  if (!Array.isArray(value)) return null;
  const flights: [string, string][] = [];
  for (const entry of value) {
    if (flights.length >= MAX_COMPARED_FLIGHTS) break;
    if (!Array.isArray(entry) || entry.length !== 2) continue;
    const [filename, color] = entry as unknown[];
    if (!isSafeName(filename, MAX_FLIGHT_FILENAME)) continue;
    if (typeof color !== 'string' || !HEX_COLOR.test(color)) continue;
    if (flights.some(([f]) => f === filename)) continue;
    flights.push([filename, color]);
  }
  return flights;
}

function trajectoryColors(value: unknown): Record<string, string> | null {
  if (!isRecord(value)) return null;
  const colors: Record<string, string> = {};
  let count = 0;
  for (const [name, color] of Object.entries(value)) {
    if (count >= MAX_TRAJECTORIES) break;
    if (!isSafeName(name, MAX_TRAJECTORY_NAME)) continue;
    if (typeof color !== 'string' || !HEX_COLOR.test(color)) continue;
    colors[name] = color;
    count++;
  }
  return colors;
}

function hiddenTrajectories(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const names: string[] = [];
  for (const name of value) {
    if (names.length >= MAX_TRAJECTORIES) break;
    if (isSafeName(name, MAX_TRAJECTORY_NAME) && !names.includes(name)) names.push(name);
  }
  return names;
}

/** A name with control characters removed and capped at `max` UTF-16 units. */
function cleanName(value: unknown, max: number): string {
  return typeof value === 'string' ? value.replace(CONTROL_CHARS, '').slice(0, max) : '';
}

function missionNames(value: unknown): { n: string; ss: string; sh: string } | null {
  if (!isRecord(value)) return null;
  const m = {
    n: cleanName(value.n, MAX_MISSION_NAME),
    ss: cleanName(value.ss, MAX_VEHICLE_NAME),
    sh: cleanName(value.sh, MAX_VEHICLE_NAME),
  };
  return m;
}

function engineSerials(value: unknown): Record<string, string> | null {
  if (!isRecord(value)) return null;
  const serials: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (!/^[1-9][0-9]?$/.test(key) || Number(key) > MAX_ENGINE_NUMBER) continue;
    const serial = typeof raw === 'string' ? normaliseSerial(raw) : '';
    if (serial) serials[key] = serial;
  }
  return serials;
}
