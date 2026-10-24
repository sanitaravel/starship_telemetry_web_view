import type { TimelineAuto, TimelineVehicle } from './types';

const SUPER_HEAVY = /\bsuper\s*heavy\b|\bbooster\b/i;
const STARSHIP = /\bstarship\b|\bship\b/i;

/** After this many seconds into the flight, a landing without a vehicle named is Starship's. */
const SHIP_LANDING_AFTER_S = 600;

/** One keyword guess; the first entry whose pattern matches (and `when` allows) decides. */
interface Guess {
  pattern: RegExp;
  /** Only for events at these times, e.g. unqualified landing events late in the flight. */
  when?: (t: number) => boolean;
  vehicle?: TimelineVehicle;
  auto?: TimelineAuto;
}

const lateFlight = (t: number) => t >= SHIP_LANDING_AFTER_S;
const manual: TimelineAuto = { kind: 'manual' };
const rule = (id: Extract<TimelineAuto, { kind: 'rule' }>['rule']): TimelineAuto => ({ kind: 'rule', rule: id });
const hud = (label: string): TimelineAuto => ({ kind: 'hud', label });

/**
 * Keyword guesses for the automation source and, where the generic
 * Super Heavy / Starship wording would be wrong, the vehicle. Order matters:
 * the most specific entry first. Extend by adding a row.
 */
export const GUESSES: readonly Guess[] = [
  { pattern: /excitement guaranteed/i, vehicle: 'both', auto: { kind: 'clock' } },
  { pattern: /engine startup/i, auto: { kind: 'clock' } },
  { pattern: /^liftoff\b/i, vehicle: 'both', auto: rule('liftoff') },
  { pattern: /\bmax\s*q\b/i, vehicle: 'both', auto: hud('MAX Q') },
  { pattern: /\bmeco\b/i, vehicle: 'superheavy', auto: rule('sh_meco') },
  { pattern: /hot[\s-]*staging|stage\s*sep/i, vehicle: 'both', auto: hud('STAGE SEP') },
  { pattern: /boostback.*\b(start|ignition)/i, vehicle: 'superheavy', auto: rule('sh_boostback_start') },
  { pattern: /boostback.*\b(shutdown|end|cutoff)/i, vehicle: 'superheavy', auto: rule('sh_boostback_end') },
  { pattern: /(super\s*heavy|booster).*landing burn.*\b(start|ignition)/i, vehicle: 'superheavy', auto: rule('sh_landing_start') },
  { pattern: /(super\s*heavy|booster).*landing burn.*\b(shutdown|end|cutoff)/i, vehicle: 'superheavy', auto: rule('sh_landing_end') },
  { pattern: /\bseco\b|starship engine cut\s*off/i, vehicle: 'starship', auto: rule('ss_seco') },
  { pattern: /payload deploy.*\bstart/i, vehicle: 'starship', auto: hud('PAYLOAD DEPLOY') },
  { pattern: /payload deploy/i, vehicle: 'starship', auto: manual },
  { pattern: /relight/i, vehicle: 'starship', auto: rule('ss_relight') },
  { pattern: /\bentry\b/i, vehicle: 'starship', auto: manual },
  { pattern: /transonic/i, vehicle: 'starship', auto: rule('ss_transonic') },
  { pattern: /subsonic/i, vehicle: 'starship', auto: rule('ss_subsonic') },
  { pattern: /landing burn.*3 to 2/i, vehicle: 'starship', auto: rule('ss_landing_3_to_2') },
  { pattern: /landing burn.*2 to 1/i, vehicle: 'starship', auto: rule('ss_landing_2_to_1') },
  { pattern: /landing flip/i, vehicle: 'starship', auto: manual },
  { pattern: /landing burn.*\bstart/i, when: lateFlight, vehicle: 'starship', auto: rule('ss_landing_start') },
  { pattern: /\blanding\b/i, when: lateFlight, vehicle: 'starship', auto: manual },
];

function firstGuess(text: string, t: number, field: 'vehicle' | 'auto'): Guess | undefined {
  return GUESSES.find((g) => g[field] !== undefined && g.pattern.test(text) && (g.when?.(t) ?? true));
}

/**
 * Which vehicle an event concerns. A keyword guess wins; otherwise the text
 * decides: only Super Heavy / Booster is Super Heavy, only Starship / Ship is
 * Starship, both or neither is both. `t` (seconds from T-0) tells a landing
 * late in the flight, with no vehicle named, from Super Heavy's.
 */
export function guessVehicle(text: string, t = 0): TimelineVehicle {
  const override = firstGuess(text, t, 'vehicle');
  if (override) return override.vehicle!;
  const superHeavy = SUPER_HEAVY.test(text);
  const starship = STARSHIP.test(text);
  if (superHeavy === starship) return 'both';
  return superHeavy ? 'superheavy' : 'starship';
}

/** What probably confirms an event: a keyword guess, otherwise a person ("manual"). */
export function guessAuto(text: string, t: number): TimelineAuto {
  const guess = firstGuess(text, t, 'auto');
  return guess ? structuredClone(guess.auto!) : { kind: 'manual' };
}
