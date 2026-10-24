import { guessAuto, guessVehicle } from './guess';
import { MAX_EVENT_TEXT, MAX_TIMELINE_SECONDS, type TimelineAuto, type TimelineEvent, type TimelineVehicle } from './types';

/** Which pasted table a row came from: the countdown counts down to T-0, the flight counts up from it. */
export type TimelineSection = 'countdown' | 'flight';

/** A parsed row, before it gets an id. */
export interface TimelineDraft {
  t: number;
  event: string;
  vehicle: TimelineVehicle;
  auto: TimelineAuto;
}

/** A line that is neither a row nor a header; shown to the user, never dropped silently. */
export interface TimelineWarning {
  /** 1-based line number within the pasted text. */
  line: number;
  text: string;
  reason: string;
}

export interface TimelineParseResult {
  events: TimelineDraft[];
  warnings: TimelineWarning[];
}

// Optional "T-" / "T+" / "-" / "+" (also the typographic minus and en dash), then H:MM:SS, HH:MM:SS or MM:SS,
// then whitespace (a tab, 2+ spaces or one space) and the event text.
const SIGNS = '+\\-\\u2212\\u2013';
const ROW = new RegExp(`^(?:T\\s*)?([${SIGNS}])?\\s*(\\d{1,2}):(\\d{2})(?::(\\d{2}))?(?:\\s+(.*))?$`, 'i');
// "Hr/Min/Sec<TAB>Event", "Countdown:", "FLIGHT TEST TIMELINE"
const HEADER = /^(?:hr\s*\/\s*min\s*\/\s*sec\b.*|(?:(?:countdown|flight(?:\s+test)?|launch)(?:\s+timeline)?|timeline)\s*:?)$/i;

function toSeconds(a: number, b: number, c: number | undefined): number | null {
  if (c === undefined) return b < 60 ? a * 60 + b : null; // MM:SS
  return b < 60 && c < 60 ? (a * 60 + b) * 60 + c : null; // H:MM:SS
}

/**
 * Reads one pasted table of "time, event" rows. The section decides the
 * sign (countdown rows are before T-0), unless the row has its own. Header
 * lines and blank lines are skipped; any other line that is not a row is
 * returned as a warning. Never throws.
 */
export function parseTimelinePaste(text: string, section: TimelineSection): TimelineParseResult {
  const events: TimelineDraft[] = [];
  const warnings: TimelineWarning[] = [];

  text.split(/\r\n|\r|\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (line === '' || HEADER.test(line)) return;
    const warn = (reason: string) => warnings.push({ line: index + 1, text: line, reason });

    const match = ROW.exec(line);
    if (!match) return warn('Not a time followed by an event');
    const [, sign, first, second, third, rest] = match;
    const seconds = toSeconds(Number(first), Number(second), third === undefined ? undefined : Number(third));
    if (seconds === null) return warn('Minutes and seconds must be below 60');
    if (seconds > MAX_TIMELINE_SECONDS) return warn('More than 6 hours from T-0');
    const event = (rest ?? '').trim();
    if (event === '') return warn('No event text');
    if (event.length > MAX_EVENT_TEXT) return warn(`Event text is longer than ${MAX_EVENT_TEXT} characters`);

    const negative = sign !== undefined ? sign !== '+' : section === 'countdown';
    const t = negative && seconds !== 0 ? -seconds : seconds;
    events.push({ t, event, vehicle: guessVehicle(event, t), auto: guessAuto(event, t) });
  });

  return { events, warnings };
}

/** "T-00:12:31" / "T+00:00:58": a time from T-0 as the mission page prints it. */
export function formatTimelineTime(t: number): string {
  const abs = Math.abs(t);
  const pad = (n: number) => String(n).padStart(2, '0');
  const clock = `${pad(Math.floor(abs / 3600))}:${pad(Math.floor(abs / 60) % 60)}:${pad(abs % 60)}`;
  return `T${t < 0 ? '-' : '+'}${clock}`;
}

/** An id like "3-booster-lox-load-underway": unique through the index, readable through the text. */
function eventId(index: number, text: string): string {
  const slug = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 30)
    .replace(/-+$/, '');
  return slug ? `${index}-${slug}` : String(index);
}

/**
 * The timeline for a mission file: the rows of both pastes in time order
 * (rows at the same time keep their order, so both T-0 rows survive), each with an id.
 */
export function buildTimeline(drafts: readonly TimelineDraft[]): TimelineEvent[] {
  return [...drafts]
    .sort((a, b) => a.t - b.t) // stable
    .map((draft, index) => ({ id: eventId(index, draft.event), ...draft }));
}

/** An id for an event added to an existing timeline, different from every id in it. */
export function nextEventId(existing: readonly TimelineEvent[], text: string): string {
  const taken = new Set(existing.map((e) => e.id));
  for (let index = existing.length; ; index++) {
    const id = eventId(index, text);
    if (!taken.has(id)) return id;
  }
}
