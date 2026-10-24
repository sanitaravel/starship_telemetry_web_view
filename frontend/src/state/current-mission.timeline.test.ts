/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { CurrentMission } from './current-mission';
import type { Vehicle } from '../domain/engines/layout';
import type { TimelineEvent } from '../domain/timeline/types';
import { EngineSerials } from './engine-serials';
import { FakeMissionsApi } from '../api/fake-missions';

let api: FakeMissionsApi;
let serials: Record<Vehicle, EngineSerials>;
let mission: CurrentMission;

const settle = () => new Promise((resolve) => setTimeout(resolve, 5));
const bothSerials = () => ({ starship: new EngineSerials('starship'), superheavy: new EngineSerials('superheavy') });

const liftoff: TimelineEvent = {
  id: '0-liftoff',
  t: 0,
  event: 'Liftoff',
  vehicle: 'both',
  auto: { kind: 'rule', rule: 'liftoff' },
};
const goForLaunch: TimelineEvent = {
  id: '1-go',
  t: -30,
  event: 'GO for launch',
  vehicle: 'both',
  auto: { kind: 'manual' },
};

beforeEach(() => {
  localStorage.clear();
  api = new FakeMissionsApi();
  serials = bothSerials();
  mission = new CurrentMission(serials, api, 0);
});

describe('the mission the backend follows', () => {
  it('is told on page load, when another mission is chosen and when it is cleared', async () => {
    expect(api.activations).toEqual([]); // unnamed: nothing to follow
    await mission.choose('IFT 15');
    await mission.choose('IFT 15'); // same mission: told once
    await mission.choose('IFT 16');
    await mission.choose('');
    expect(api.activations).toEqual(['ift-15', 'ift-16', null]);

    await mission.choose('IFT 15');
    const reloaded = new CurrentMission(bothSerials(), api, 0);
    await settle();
    expect(api.activations.at(-1)).toBe('ift-15');
    expect(reloaded.mission).toBe('IFT 15');
  });

  it('is told again after the timeline was saved, but not after other edits', async () => {
    await mission.choose('IFT 15');
    mission.setVehicleName('starship', 'S40');
    await mission.flush();
    expect(api.activations).toEqual(['ift-15']);
    mission.setTimeline([liftoff]);
    await mission.flush();
    expect(api.activations).toEqual(['ift-15', 'ift-15']);
  });

  it('retries after the backend could not be told', async () => {
    api.offline = true;
    await mission.choose('IFT 15').catch(() => undefined);
    await settle();
    api.offline = false;
    mission.setTimeline([liftoff]);
    await mission.flush();
    expect(api.activations).toEqual(['ift-15']);
  });
});

describe('the mission timeline', () => {
  it('starts empty and is saved with the mission file', async () => {
    expect(mission.timeline).toEqual([]);
    await mission.choose('IFT 15');
    mission.setTimeline([goForLaunch, liftoff]);
    await mission.flush();

    expect(api.files.get('ift-15')!.timeline).toEqual([goForLaunch, liftoff]);
    expect(mission.status.kind).toBe('saved');
  });

  it('tells subscribers about every change, until they unsubscribe', async () => {
    await mission.choose('IFT 15');
    let calls = 0;
    const unsubscribe = mission.subscribeTimeline(() => calls++);
    mission.setTimeline([liftoff]);
    expect(calls).toBe(1);
    unsubscribe();
    mission.setTimeline([]);
    expect(calls).toBe(1);
    await mission.flush();
  });

  it('choosing a saved mission loads its timeline without saving it back', async () => {
    api.files.set('ift-14', {
      id: 'ift-14',
      mission: 'IFT 14',
      ship: '',
      booster: '',
      starship_engines: {},
      timeline: [liftoff],
      updated_at: null,
    });
    let calls = 0;
    mission.subscribeTimeline(() => calls++);

    await mission.choose('IFT 14');
    await settle();

    expect(mission.timeline).toEqual([liftoff]);
    expect(calls).toBe(1);
    expect(api.saves).toHaveLength(0);
  });

  it('a file saved before timelines existed gives an empty one', async () => {
    api.files.set('ift-13', {
      id: 'ift-13',
      mission: 'IFT 13',
      ship: '',
      booster: '',
      starship_engines: {},
      updated_at: null,
    });
    await mission.choose('IFT 13');
    expect(mission.timeline).toEqual([]);
  });

  it('a new mission started from another named one does not inherit its timeline', async () => {
    await mission.choose('IFT 15');
    mission.setTimeline([liftoff]);
    await mission.flush();

    await mission.choose('IFT 16');
    await mission.flush();

    expect(mission.timeline).toEqual([]);
    expect(api.files.get('ift-16')!.timeline).toEqual([]);
    expect(api.files.get('ift-15')!.timeline).toEqual([liftoff]);
  });

  it('is read from the file after a page load, and a serial edit right away does not wipe it', async () => {
    await mission.choose('IFT 15');
    mission.setTimeline([liftoff]);
    await mission.flush();

    const reloadedSerials = bothSerials();
    const reloaded = new CurrentMission(reloadedSerials, api, 0);
    expect(reloaded.timeline).toEqual([]); // the file is still being read
    reloadedSerials.starship.set('ss_e1', 'R1');
    await reloaded.flush();
    await settle();

    expect(reloaded.timeline).toEqual([liftoff]);
    expect(api.files.get('ift-15')).toMatchObject({ starship_engines: { ss_e1: 'R1' }, timeline: [liftoff] });
  });

  it('keeps the timeline when only a serial changes', async () => {
    await mission.choose('IFT 15');
    mission.setTimeline([liftoff]);
    serials.starship.set('ss_e6', 'R576');
    await mission.flush();

    expect(api.files.get('ift-15')).toMatchObject({ starship_engines: { ss_e6: 'R576' }, timeline: [liftoff] });
  });
});
