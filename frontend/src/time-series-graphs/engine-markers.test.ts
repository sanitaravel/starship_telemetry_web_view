import { describe, it, expect } from 'vitest';
import type { EngineEvent } from '../engine-events';
import {
  ENGINE_OUT_COLOR,
  eventMarkers,
  eventsForSeries,
  FAILED_TO_LIGHT_COLOR,
  formatEngineEvent,
  seriesVehicle,
} from './engine-markers';

function event(overrides: Partial<EngineEvent> = {}): EngineEvent {
  return {
    kind: 'engine_out',
    vehicle: 'superheavy',
    engineIds: ['sh_e17'],
    rings: ['engines_outer'],
    metSeconds: 112,
    runningAfter: 32,
    expected: 33,
    ...overrides,
  };
}

describe('seriesVehicle', () => {
  it('maps series keys to their vehicle', () => {
    expect(seriesVehicle('speedSuperHeavy')).toBe('superheavy');
    expect(seriesVehicle('altitudeSuperHeavy')).toBe('superheavy');
    expect(seriesVehicle('accelerationSuperHeavy')).toBe('superheavy');
    expect(seriesVehicle('speedStarship')).toBe('starship');
    expect(seriesVehicle('accelerationStarship')).toBe('starship');
  });
});

describe('eventsForSeries', () => {
  const shEarly = event({ metSeconds: 60 });
  const shLate = event({ metSeconds: 200 });
  const ssEvent = event({ vehicle: 'starship', engineIds: ['ss_e2'], rings: ['atmo'], metSeconds: 400 });
  const all = [shEarly, ssEvent, shLate];

  it('shows only Super Heavy events on Super Heavy series', () => {
    expect(eventsForSeries(all, 'superheavy', 160)).toEqual([shEarly, shLate]);
    expect(eventsForSeries(all, 'superheavy', null)).toEqual([shEarly, shLate]);
  });

  it('shows Super Heavy events before separation and Starship events on Starship series', () => {
    expect(eventsForSeries(all, 'starship', 160)).toEqual([shEarly, ssEvent]);
  });

  it('shows every Super Heavy event on Starship series while separation is unknown', () => {
    expect(eventsForSeries(all, 'starship', null)).toEqual(all);
  });
});

describe('formatEngineEvent', () => {
  it('formats an engine out', () => {
    expect(formatEngineEvent(event())).toBe(
      'Engine out: Super Heavy E17 (outer ring) · T+1:52 · 32/33 running',
    );
  });

  it('formats an engine out after relight', () => {
    expect(formatEngineEvent(event({ kind: 'died_after_relight', runningAfter: 12, expected: 13 }))).toBe(
      'Engine out after relight: Super Heavy E17 (outer ring) · T+1:52 · 12/13 running',
    );
  });

  it('formats a failure to light', () => {
    expect(
      formatEngineEvent(event({
        kind: 'failed_to_light',
        engineIds: ['sh_e7'],
        rings: ['engines_middle'],
        metSeconds: 387,
        runningAfter: 12,
        expected: 13,
      })),
    ).toBe('Failed to light: Super Heavy E7 (middle ring) · T+6:27 · 12/13 running');
  });

  it('lists several engines of one ring once', () => {
    expect(formatEngineEvent(event({ engineIds: ['sh_e17', 'sh_e22'], runningAfter: 31 }))).toBe(
      'Engine out: Super Heavy E17, E22 (outer ring) · T+1:52 · 31/33 running',
    );
  });

  it('groups engines by ring', () => {
    expect(
      formatEngineEvent(event({
        engineIds: ['sh_e5', 'sh_e17'],
        rings: ['engines_middle', 'engines_outer'],
        runningAfter: 31,
      })),
    ).toBe('Engine out: Super Heavy E5 (middle ring), E17 (outer ring) · T+1:52 · 31/33 running');
  });

  it('names Starship engines', () => {
    expect(
      formatEngineEvent(event({ vehicle: 'starship', engineIds: ['ss_e2'], rings: ['atmo'], runningAfter: 5, expected: 6 })),
    ).toBe('Engine out: Starship E2 (sea-level) · T+1:52 · 5/6 running');
  });

  it('gives the gap for events inside a panel gap', () => {
    expect(formatEngineEvent(event({ metSeconds: 175, metRange: [161, 175] }))).toBe(
      'Engine out: Super Heavy E17 (outer ring) · between T+2:41 and T+2:55 · 32/33 running',
    );
  });

  it('prefixes comparison flights with their name', () => {
    expect(formatEngineEvent(event(), 'IFT-12')).toBe(
      'IFT-12 — Engine out: Super Heavy E17 (outer ring) · T+1:52 · 32/33 running',
    );
  });
});

describe('eventMarkers', () => {
  it('colours live markers by kind and keeps gap ranges', () => {
    const markers = eventMarkers([
      event(),
      event({ kind: 'failed_to_light', metSeconds: 387, metRange: [380, 387] }),
    ]);
    expect(markers).toEqual([
      { x: 112, color: ENGINE_OUT_COLOR, kind: 'engine_out', tooltipLines: [formatEngineEvent(event())] },
      expect.objectContaining({ x: 387, range: [380, 387], color: FAILED_TO_LIGHT_COLOR, kind: 'failed_to_light' }),
    ]);
  });

  it('uses the comparison flight colour and name', () => {
    const [marker] = eventMarkers([event()], '#00bcd4', 'IFT-12');
    expect(marker.color).toBe('#00bcd4');
    expect(marker.tooltipLines).toEqual([formatEngineEvent(event(), 'IFT-12')]);
  });
});

describe('formatEngineEvent: more shapes', () => {
  it('combines kind, multi-ring engines, a gap range and a comparison prefix', () => {
    expect(
      formatEngineEvent(
        event({
          kind: 'died_after_relight',
          engineIds: ['sh_e5', 'sh_e6', 'sh_e21', 'sh_e22'],
          rings: ['engines_middle', 'engines_outer'],
          metSeconds: 153,
          metRange: [150, 153],
          runningAfter: 8,
          expected: 20,
        }),
        'IFT-12',
      ),
    ).toBe(
      'IFT-12 — Engine out after relight: Super Heavy E5, E6 (middle ring), E21, E22 (outer ring)' +
        ' · between T+2:30 and T+2:33 · 8/20 running',
    );
  });

  it('formats a several-engine failure to light', () => {
    expect(
      formatEngineEvent(event({
        kind: 'failed_to_light',
        engineIds: ['sh_e11', 'sh_e12', 'sh_e13'],
        rings: ['engines_middle'],
        metSeconds: 400,
        runningAfter: 10,
        expected: 13,
      })),
    ).toBe('Failed to light: Super Heavy E11, E12, E13 (middle ring) · T+6:40 · 10/13 running');
  });

  it('uses an hours clock for the ship landing burn', () => {
    expect(
      formatEngineEvent(event({
        kind: 'failed_to_light',
        vehicle: 'starship',
        engineIds: ['ss_e3'],
        rings: ['atmo'],
        metSeconds: 3906,
        runningAfter: 2,
        expected: 3,
      })),
    ).toBe('Failed to light: Starship E3 (sea-level) · T+1:05:06 · 2/3 running');
  });

  it('groups sea-level and vacuum ship engines', () => {
    expect(
      formatEngineEvent(event({
        vehicle: 'starship',
        engineIds: ['ss_e3', 'ss_e5', 'ss_e6'],
        rings: ['atmo', 'vacuum'],
        metSeconds: 190,
        runningAfter: 3,
        expected: 6,
      })),
    ).toBe('Engine out: Starship E3 (sea-level), E5, E6 (vacuum) · T+3:10 · 3/6 running');
  });

  it('rounds sub-second MET, and formats a range on a failure to light with a prefix', () => {
    expect(formatEngineEvent(event({ metSeconds: 101.6 }))).toBe(
      'Engine out: Super Heavy E17 (outer ring) · T+1:42 · 32/33 running',
    );
    expect(
      formatEngineEvent(event({ kind: 'failed_to_light', metSeconds: 3600, metRange: [3599.4, 3600.2] }), 'IFT-13'),
    ).toBe('IFT-13 — Failed to light: Super Heavy E17 (outer ring) · between T+59:59 and T+1:00:00 · 32/33 running');
  });

  it('copies the gap range into the marker instead of aliasing it', () => {
    const e = event({ metRange: [10, 20] });
    const [marker] = eventMarkers([e], '#00bcd4', 'IFT-12');
    expect(marker.range).toEqual([10, 20]);
    expect(marker.range).not.toBe(e.metRange);
    expect(marker.tooltipLines[0].startsWith('IFT-12 — Engine out: ')).toBe(true);
  });
});
