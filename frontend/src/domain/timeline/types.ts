/** The mission timeline as a mission file stores it (see backend/src/storage/missions.py). */

export type TimelineVehicle = 'starship' | 'superheavy' | 'both';

export const TIMELINE_VEHICLES: readonly TimelineVehicle[] = ['starship', 'superheavy', 'both'];

/**
 * Telemetry rules an event can be confirmed by. Mirrors TIMELINE_RULES in
 * backend/src/storage/missions.py (a test compares the two), with a short
 * description for each.
 */
export const TIMELINE_RULES = [
  'liftoff',
  'sh_meco',
  'sh_boostback_start',
  'sh_boostback_end',
  'sh_landing_start',
  'sh_landing_end',
  'ss_seco',
  'ss_relight',
  'ss_landing_start',
  'ss_landing_3_to_2',
  'ss_landing_2_to_1',
  'ss_transonic',
  'ss_subsonic',
] as const;

export type TimelineRule = (typeof TIMELINE_RULES)[number];

export const TIMELINE_RULE_LABELS: Record<TimelineRule, string> = {
  liftoff: 'Liftoff',
  sh_meco: 'Super Heavy MECO',
  sh_boostback_start: 'Boostback start',
  sh_boostback_end: 'Boostback end',
  sh_landing_start: 'Super Heavy landing burn start',
  sh_landing_end: 'Super Heavy landing burn end',
  ss_seco: 'Starship SECO',
  ss_relight: 'Raptor relight',
  ss_landing_start: 'Starship landing burn start',
  ss_landing_3_to_2: 'Landing burn 3 to 2 engines',
  ss_landing_2_to_1: 'Landing burn 2 to 1 engine',
  ss_transonic: 'Transonic',
  ss_subsonic: 'Subsonic',
};

/** What confirms an event: a person, the mission clock, a HUD milestone label or a telemetry rule. */
export type TimelineAuto =
  | { kind: 'manual' }
  | { kind: 'clock' }
  | { kind: 'hud'; label: string }
  | { kind: 'rule'; rule: TimelineRule };

export type TimelineAutoKind = TimelineAuto['kind'];

export const TIMELINE_AUTO_KINDS: readonly TimelineAutoKind[] = ['manual', 'clock', 'hud', 'rule'];

/** One planned event, `t` seconds from T-0 (negative in the countdown). */
export interface TimelineEvent {
  /** Unique within the mission; stable for as long as the event exists. */
  id: string;
  t: number;
  event: string;
  vehicle: TimelineVehicle;
  auto: TimelineAuto;
}

/** Limits the backend enforces. */
export const MAX_TIMELINE_EVENTS = 200;
export const MAX_TIMELINE_SECONDS = 6 * 3600;
export const MAX_EVENT_TEXT = 200;
export const MAX_HUD_LABEL = 40;
