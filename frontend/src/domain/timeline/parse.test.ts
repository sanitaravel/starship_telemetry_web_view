import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import fc from 'fast-check';
import { buildTimeline, formatTimelineTime, nextEventId, parseTimelinePaste, type TimelineDraft, type TimelineSection } from './parse';
import { guessAuto, guessVehicle } from './guess';
import { COUNTDOWN_PASTE, FLIGHT_PASTE } from './fixtures';
import { MAX_TIMELINE_SECONDS, TIMELINE_RULES } from './types';

const byText = (events: TimelineDraft[], start: string) => {
  const found = events.find((e) => e.event.startsWith(start));
  if (!found) throw new Error(`no event starting with "${start}"`);
  return found;
};

describe('parseTimelinePaste: the SpaceX mission page tables', () => {
  const countdown = parseTimelinePaste(COUNTDOWN_PASTE, 'countdown');
  const flight = parseTimelinePaste(FLIGHT_PASTE, 'flight');

  it('reads every row and warns about nothing', () => {
    expect(countdown.warnings).toEqual([]);
    expect(flight.warnings).toEqual([]);
    expect(countdown.events).toHaveLength(12);
    expect(flight.events).toHaveLength(20);
  });

  it('counts the countdown down from T-0, with T-0 itself at 0', () => {
    expect(countdown.events.map((e) => e.t)).toEqual([
      -3000, -2250, -2220, -2125, -2088, -1290, -170, -130, -30, -17, -3, 0,
    ]);
    expect(Object.is(countdown.events.at(-1)!.t, 0)).toBe(true);
    expect(flight.events[0].t).toBe(0);
    expect(byText(flight.events, 'Starship is subsonic').t).toBe(3781);
  });

  it('guesses the vehicle', () => {
    const v = (events: TimelineDraft[], start: string) => byText(events, start).vehicle;
    expect(v(countdown.events, 'Raptor begins engine chill')).toBe('both');
    expect(v(countdown.events, 'Ship LOX')).toBe('starship');
    expect(v(countdown.events, 'Booster LOX')).toBe('superheavy');
    expect(v(countdown.events, 'SpaceX Flight Director')).toBe('both');
    expect(v(flight.events, 'Hot-staging')).toBe('both');
    expect(v(flight.events, 'Super Heavy MECO')).toBe('superheavy');
    expect(v(flight.events, 'Starship engine cutoff')).toBe('starship');
    expect(v(flight.events, 'Raptor in-space relight')).toBe('starship');
    expect(v(flight.events, 'Landing burn start')).toBe('starship');
    expect(v(flight.events, 'An exciting landing')).toBe('starship');
  });

  it('guesses the automation source', () => {
    const a = (events: TimelineDraft[], start: string) => byText(events, start).auto;
    expect(a(countdown.events, 'Excitement guaranteed')).toEqual({ kind: 'clock' });
    expect(a(countdown.events, 'Booster engine startup')).toEqual({ kind: 'clock' });
    expect(a(countdown.events, 'Ship LOX')).toEqual({ kind: 'manual' });
    expect(a(flight.events, 'Liftoff')).toEqual({ kind: 'rule', rule: 'liftoff' });
    expect(a(flight.events, 'Max Q')).toEqual({ kind: 'hud', label: 'MAX Q' });
    expect(a(flight.events, 'Super Heavy MECO')).toEqual({ kind: 'rule', rule: 'sh_meco' });
    expect(a(flight.events, 'Hot-staging')).toEqual({ kind: 'hud', label: 'STAGE SEP' });
    expect(a(flight.events, 'Super Heavy boostback burn start')).toEqual({ kind: 'rule', rule: 'sh_boostback_start' });
    expect(a(flight.events, 'Super Heavy boostback burn shutdown')).toEqual({ kind: 'rule', rule: 'sh_boostback_end' });
    expect(a(flight.events, 'Super Heavy landing burn start')).toEqual({ kind: 'rule', rule: 'sh_landing_start' });
    expect(a(flight.events, 'Super Heavy landing burn shutdown')).toEqual({ kind: 'rule', rule: 'sh_landing_end' });
    expect(a(flight.events, 'Starship engine cutoff')).toEqual({ kind: 'rule', rule: 'ss_seco' });
    expect(a(flight.events, 'Payload deploy demo start')).toEqual({ kind: 'hud', label: 'PAYLOAD DEPLOY' });
    expect(a(flight.events, 'Payload deploy demo complete')).toEqual({ kind: 'manual' });
    expect(a(flight.events, 'Raptor in-space relight')).toEqual({ kind: 'rule', rule: 'ss_relight' });
    expect(a(flight.events, 'Starship entry')).toEqual({ kind: 'manual' });
    expect(a(flight.events, 'Starship is transonic')).toEqual({ kind: 'rule', rule: 'ss_transonic' });
    expect(a(flight.events, 'Starship is subsonic')).toEqual({ kind: 'rule', rule: 'ss_subsonic' });
    expect(a(flight.events, 'Landing burn start')).toEqual({ kind: 'rule', rule: 'ss_landing_start' });
    expect(a(flight.events, 'Landing flip')).toEqual({ kind: 'manual' });
    expect(a(flight.events, 'Landing burn 3 to 2')).toEqual({ kind: 'rule', rule: 'ss_landing_3_to_2' });
    expect(a(flight.events, 'Landing burn 2 to 1')).toEqual({ kind: 'rule', rule: 'ss_landing_2_to_1' });
    expect(a(flight.events, 'An exciting landing')).toEqual({ kind: 'manual' });
  });

  it('merges into a sorted timeline keeping both T-0 rows and unique ids', () => {
    const timeline = buildTimeline([...flight.events, ...countdown.events]);
    expect(timeline).toHaveLength(32);
    const t0 = timeline.filter((e) => e.t === 0).map((e) => e.event);
    expect(t0).toEqual(['Liftoff', 'Excitement guaranteed']); // input order: flight first here
    expect(timeline.map((e) => e.t)).toEqual([...timeline.map((e) => e.t)].sort((a, b) => a - b));
    expect(new Set(timeline.map((e) => e.id)).size).toBe(32);
    for (const e of timeline) expect(e.id).toMatch(/^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/);
  });
});

describe('parseTimelinePaste: row formats', () => {
  const one = (text: string, section: 'countdown' | 'flight' = 'flight') => parseTimelinePaste(text, section);

  it.each([
    ['00:02:18\tMECO', 'flight', 138],
    ['2:18\tMECO', 'flight', 138],
    ['1:02:18 MECO', 'flight', 3738],
    ['00:02:18   MECO', 'flight', 138],
    ['T-00:00:30\tGO', 'flight', -30],
    ['T+00:00:30\tGO', 'countdown', 30],
    ['-00:30 GO', 'flight', -30],
    ['+00:30 GO', 'countdown', 30],
    ['−00:30 GO', 'flight', -30],
    ['T- 00:30 GO', 'flight', -30],
    ['00:30\tGO', 'countdown', -30],
    ['  00:30\tGO  \r', 'flight', 30],
  ] as const)('%j in the %s section is at %i s', (line, section, t) => {
    const result = one(line, section);
    expect(result.warnings).toEqual([]);
    expect(result.events.map((e) => e.t)).toEqual([t]);
  });

  it('skips headers and blank lines silently', () => {
    const result = one('FLIGHT TEST TIMELINE\n\nHr/Min/Sec\tEvent\nCountdown:\n   \n00:00:00\tLiftoff');
    expect(result.warnings).toEqual([]);
    expect(result.events).toHaveLength(1);
  });

  it('warns, with the line number, about everything else', () => {
    const result = one('00:00:00\tLiftoff\nsomething odd\n00:75:00\tBad minutes\n00:10:00\n99:00:00\tToo far\n00:20:00\tFine');
    expect(result.events.map((e) => e.event)).toEqual(['Liftoff', 'Fine']);
    expect(result.warnings.map((w) => [w.line, w.text])).toEqual([
      [2, 'something odd'],
      [3, '00:75:00\tBad minutes'],
      [4, '00:10:00'],
      [5, '99:00:00\tToo far'],
    ]);
  });

  it('warns about text longer than the backend accepts', () => {
    const result = one(`00:10:00\t${'x'.repeat(201)}`);
    expect(result.events).toEqual([]);
    expect(result.warnings).toHaveLength(1);
  });
});

describe('guessVehicle / guessAuto', () => {
  it('uses the words Super Heavy, Booster, Starship and Ship', () => {
    expect(guessVehicle('Super Heavy something')).toBe('superheavy');
    expect(guessVehicle('the BOOSTER something')).toBe('superheavy');
    expect(guessVehicle('Starship something')).toBe('starship');
    expect(guessVehicle('Ship LOX load underway')).toBe('starship');
    expect(guessVehicle('booster and ship')).toBe('both');
    expect(guessVehicle('something else')).toBe('both');
    expect(guessVehicle('Relationship status')).toBe('both'); // "ship" only as a whole word
  });

  it('tells an unqualified landing late in the flight from an early one', () => {
    expect(guessVehicle('Landing burn start', 3901)).toBe('starship');
    expect(guessAuto('Landing burn start', 3901)).toEqual({ kind: 'rule', rule: 'ss_landing_start' });
    expect(guessAuto('Landing burn start', 60)).toEqual({ kind: 'manual' });
  });

  it('only suggests rules the backend knows', () => {
    for (const text of [COUNTDOWN_PASTE, FLIGHT_PASTE]) {
      for (const e of parseTimelinePaste(text, 'flight').events) {
        if (e.auto.kind === 'rule') expect(TIMELINE_RULES).toContain(e.auto.rule);
      }
    }
  });
});

describe('formatTimelineTime / nextEventId', () => {
  it('prints T-/T+ times', () => {
    expect(formatTimelineTime(-751)).toBe('T-00:12:31');
    expect(formatTimelineTime(58)).toBe('T+00:00:58');
    expect(formatTimelineTime(3781)).toBe('T+01:03:01');
  });

  it('never repeats an id', () => {
    const timeline = buildTimeline(parseTimelinePaste(FLIGHT_PASTE, 'flight').events);
    expect(timeline.map((e) => e.id)).not.toContain(nextEventId(timeline, 'Liftoff'));
  });
});

describe('rule ids', () => {
  it('match the backend list', () => {
    const source = readFileSync(
      fileURLToPath(new URL('../../../../backend/src/storage/missions.py', import.meta.url)),
      'utf-8'
    );
    const block = /TIMELINE_RULES = \(([^)]*)\)/.exec(source)![1];
    const backend = [...block.matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
    expect([...TIMELINE_RULES]).toEqual(backend);
  });
});

describe('properties', () => {
  const secondsArb = fc.integer({ min: 0, max: MAX_TIMELINE_SECONDS });
  const textArb = fc
    .string({ minLength: 1, maxLength: 80 })
    .map((s) => s.replace(/[\r\n\t]/g, ' ').trim())
    .filter((s) => s.length > 0);

  it('format then parse gives the same rows', () => {
    fc.assert(
      fc.property(
        fc.array(fc.tuple(secondsArb, fc.boolean(), textArb), { maxLength: 30 }),
        fc.constantFrom<TimelineSection>('countdown', 'flight'),
        (rows, section) => {
          const lines = rows.map(([s, negative, text]) => `${formatTimelineTime(negative && s ? -s : s)}\t${text}`);
          const { events, warnings } = parseTimelinePaste(lines.join('\n'), section);
          expect(warnings).toEqual([]);
          expect(events.map((e) => [e.t, e.event])).toEqual(
            rows.map(([s, negative, text]) => [negative && s ? -s : s, text])
          );
        }
      )
    );
  });

  it('without a sign, the section decides', () => {
    fc.assert(
      fc.property(secondsArb, textArb, fc.constantFrom<TimelineSection>('countdown', 'flight'), (s, text, section) => {
        const clock = formatTimelineTime(s).slice(2);
        const { events } = parseTimelinePaste(`${clock}\t${text}`, section);
        expect(events[0].t).toBe(section === 'countdown' && s ? -s : s);
        expect(Object.is(events[0].t, -0)).toBe(false);
      })
    );
  });

  it('never throws, and every line is a row, a warning or a skipped header', () => {
    fc.assert(
      fc.property(fc.string({ maxLength: 400 }), fc.constantFrom<TimelineSection>('countdown', 'flight'), (text, section) => {
        const { events, warnings } = parseTimelinePaste(text, section);
        const nonBlank = text.split(/\r\n|\r|\n/).filter((l) => l.trim() !== '');
        expect(events.length + warnings.length).toBeLessThanOrEqual(nonBlank.length);
        for (const w of warnings) expect(w.line).toBeGreaterThanOrEqual(1);
        for (const e of events) expect(Math.abs(e.t)).toBeLessThanOrEqual(MAX_TIMELINE_SECONDS);
      })
    );
  });

  it('a non-header line that is not a row is always a warning', () => {
    fc.assert(
      fc.property(
        fc.array(fc.oneof(fc.string({ maxLength: 40 }), fc.tuple(secondsArb, textArb).map(([s, t]) => `${formatTimelineTime(s)} ${t}`)), {
          maxLength: 15,
        }),
        (lines) => {
          // A leading "x" makes a line neither blank nor a header
          const body = lines.map((l) => 'x' + l.replace(/[\r\n]/g, ' '));
          const { events, warnings } = parseTimelinePaste(body.join('\n'), 'flight');
          expect(events.length + warnings.length).toBe(body.length);
          expect(warnings.map((w) => w.line)).toEqual(body.map((_, i) => i + 1));
        }
      )
    );
  });
});
