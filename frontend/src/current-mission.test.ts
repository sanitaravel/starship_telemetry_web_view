/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { CurrentMission } from './current-mission';
import type { Vehicle } from './domain/engines/layout';
import { EngineSerials } from './engine-serials';
import { FakeMissionsApi } from './fake-missions-api';

let api: FakeMissionsApi;
let serials: Record<Vehicle, EngineSerials>;
let mission: CurrentMission;

/** Lets pending timers (delay 0) and promise chains run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 5));

/** Both vehicles' serial stores, read from and written to this browser's storage. */
const bothSerials = () => ({ starship: new EngineSerials('starship'), superheavy: new EngineSerials('superheavy') });

beforeEach(() => {
  localStorage.clear();
  api = new FakeMissionsApi();
  serials = bothSerials();
  mission = new CurrentMission(serials, api, 0);
});

describe('CurrentMission', () => {
  it('without a mission name, edits stay in the browser and nothing is saved', async () => {
    serials.starship.set('ss_e6', 'R576');
    await settle();

    expect(api.saves).toHaveLength(0);
    expect(mission.status).toEqual({ kind: 'unnamed' });
  });

  it('naming a new mission saves the current ship and serials to its file', async () => {
    serials.starship.set('ss_e6', 'R576');
    mission.setVehicleName('starship', 'S35');
    await mission.choose('IFT 15');
    await mission.flush();

    expect(api.files.get('ift-15')).toMatchObject({
      mission: 'IFT 15',
      ship: 'S35',
      starship_engines: { ss_e6: 'R576' },
    });
    expect(mission.status).toEqual({ kind: 'saved', file: 'missions/engines_ift-15.json' });
  });

  it('serial edits of a named mission are saved, latest value last', async () => {
    await mission.choose('IFT 15');
    await mission.flush();
    serials.starship.set('ss_e6', 'R5');
    serials.starship.set('ss_e6', 'R57');
    serials.starship.set('ss_e6', 'R576');
    await mission.flush();

    expect(api.files.get('ift-15')!.starship_engines).toEqual({ ss_e6: 'R576' });
  });

  it('choosing a saved mission loads its ship and serials without saving them back', async () => {
    api.files.set('ift-14', {
      id: 'ift-14',
      mission: 'IFT 14',
      ship: 'S33',
      booster: '',
      starship_engines: { ss_e1: 'R101', ss_e5: 'R575' },
      updated_at: null,
    });
    serials.starship.set('ss_e6', 'R999'); // unnamed working set, replaced by the load

    await mission.choose('ift 14'); // matched by file name, not exact spelling
    await settle();

    expect(mission.mission).toBe('IFT 14');
    expect(mission.vehicleName('starship')).toBe('S33');
    expect(serials.starship.snapshot()).toEqual({ ss_e1: 'R101', ss_e5: 'R575' });
    expect(api.saves).toHaveLength(0);
    expect(mission.status).toEqual({ kind: 'idle', file: 'missions/engines_ift-14.json' });
  });

  it('switching missions writes the previous one’s pending edit to its own file first', async () => {
    api.files.set('ift-14', { id: 'ift-14', mission: 'IFT 14', ship: 'S33', booster: '', starship_engines: {}, updated_at: null });
    const slow = new CurrentMission(serials, api, 10_000); // edit would otherwise wait 10 s
    await slow.choose('IFT 15');
    await slow.flush();
    serials.starship.set('ss_e6', 'R576');

    await slow.choose('IFT 14');

    expect(api.files.get('ift-15')!.starship_engines).toEqual({ ss_e6: 'R576' });
    expect(api.files.get('ift-14')!.starship_engines).toEqual({});
  });

  it('a renamed spelling of the same mission keeps its file', async () => {
    await mission.choose('ift 15');
    await mission.flush();
    await mission.choose('IFT 15');
    await mission.flush();

    expect([...api.files.keys()]).toEqual(['ift-15']);
    expect(api.files.get('ift-15')!.mission).toBe('IFT 15');
  });

  it('clearing the name stops saving', async () => {
    await mission.choose('IFT 15');
    await mission.flush();
    await mission.choose('  ');
    serials.starship.set('ss_e1', 'R101');
    await settle();

    expect(mission.mission).toBe('');
    expect(mission.status).toEqual({ kind: 'unnamed' });
    expect(api.files.get('ift-15')!.starship_engines).toEqual({});
  });

  it('remembers the mission and ship across page loads', async () => {
    await mission.choose('IFT 15');
    mission.setVehicleName('starship', 'S36');
    await mission.flush();

    const reloaded = new CurrentMission(bothSerials(), api, 0);
    expect(reloaded.mission).toBe('IFT 15');
    expect(reloaded.vehicleName('starship')).toBe('S36');
    expect(reloaded.status).toEqual({ kind: 'idle', file: 'missions/engines_ift-15.json' });
  });

  it('reports a backend it cannot reach and saves on the next change', async () => {
    api.offline = true;
    await mission.choose('IFT 15');
    await mission.flush();
    expect(mission.status).toEqual({ kind: 'error', message: 'Not saved: backend unreachable' });

    api.offline = false;
    serials.starship.set('ss_e6', 'R576');
    await mission.flush();
    expect(api.files.get('ift-15')!.starship_engines).toEqual({ ss_e6: 'R576' });
    expect(mission.status.kind).toBe('saved');
  });

  it('writes one save at a time, in order', async () => {
    await mission.choose('IFT 15');
    await mission.flush();
    api.hold();
    serials.starship.set('ss_e6', 'R1');
    await settle(); // first save starts and waits
    serials.starship.set('ss_e6', 'R2');
    await settle();
    api.release();
    await mission.flush();

    expect(api.saves.slice(-2).map((s) => s.starship_engines.ss_e6)).toEqual(['R1', 'R2']);
    expect(api.files.get('ift-15')!.starship_engines.ss_e6).toBe('R2');
  });
});

describe('starting a new mission', () => {
  it('takes over what was typed before any mission was named', async () => {
    serials.starship.set('ss_e6', 'R576');
    mission.setVehicleName('starship', 'S36');
    await mission.choose('IFT 16');
    await mission.flush();

    expect(api.files.get('ift-16')).toMatchObject({ ship: 'S36', starship_engines: { ss_e6: 'R576' } });
  });

  it('starts empty when coming from another named mission, which keeps its file', async () => {
    await mission.choose('IFT 15');
    mission.setVehicleName('starship', 'S36');
    serials.starship.set('ss_e6', 'R576');
    await mission.flush();

    await mission.choose('IFT 16');
    await mission.flush();

    expect(mission.vehicleName('starship')).toBe('');
    expect(serials.starship.isEmpty).toBe(true);
    expect(api.files.get('ift-16')).toMatchObject({ ship: '', starship_engines: {} });
    expect(api.files.get('ift-15')).toMatchObject({ ship: 'S36', starship_engines: { ss_e6: 'R576' } });
  });
});

describe('Super Heavy in the same mission', () => {
  it('saves the booster and its serials in the mission file with the ship’s', async () => {
    await mission.choose('IFT 15');
    mission.setVehicleName('starship', 'S41');
    mission.setVehicleName('superheavy', 'B15');
    serials.starship.set('ss_e6', 'R171');
    serials.superheavy.set('sh_e33', 'R333');
    await mission.flush();

    expect(api.files.get('ift-15')).toMatchObject({
      ship: 'S41',
      booster: 'B15',
      starship_engines: { ss_e6: 'R171' },
      superheavy_engines: { sh_e33: 'R333' },
    });
  });

  it('choosing a saved mission loads both vehicles', async () => {
    api.files.set('ift-14', {
      id: 'ift-14',
      mission: 'IFT 14',
      ship: 'S41',
      booster: 'B15',
      starship_engines: { ss_e1: 'R172' },
      superheavy_engines: { sh_e1: 'R301' },
      updated_at: null,
    });

    await mission.choose('IFT 14');

    expect(mission.vehicleName('superheavy')).toBe('B15');
    expect(serials.superheavy.snapshot()).toEqual({ sh_e1: 'R301' });
    expect(serials.starship.snapshot()).toEqual({ ss_e1: 'R172' });
    expect(api.saves).toHaveLength(0);
  });

  it('a file saved before Super Heavy had settings loads with no booster serials', async () => {
    serials.superheavy.set('sh_e1', 'R999'); // unnamed working set
    api.files.set('ift-14', {
      id: 'ift-14',
      mission: 'IFT 14',
      ship: 'S41',
      booster: '',
      starship_engines: { ss_e1: 'R172' },
      updated_at: null,
    });

    await mission.choose('IFT 14');

    expect(serials.superheavy.isEmpty).toBe(true);
    expect(serials.starship.snapshot()).toEqual({ ss_e1: 'R172' });
  });

  it('a new mission from a named one starts with neither vehicle', async () => {
    await mission.choose('IFT 15');
    mission.setVehicleName('superheavy', 'B15');
    serials.superheavy.set('sh_e1', 'R301');
    await mission.flush();

    await mission.choose('IFT 16');
    await mission.flush();

    expect(mission.vehicleName('superheavy')).toBe('');
    expect(serials.superheavy.isEmpty).toBe(true);
    expect(api.files.get('ift-15')).toMatchObject({ booster: 'B15', superheavy_engines: { sh_e1: 'R301' } });
  });

  it('remembers the booster across page loads', async () => {
    await mission.choose('IFT 15');
    mission.setVehicleName('superheavy', 'B16');
    await mission.flush();

    expect(new CurrentMission(bothSerials(), api, 0).vehicleName('superheavy')).toBe('B16');
  });
});
