import { describe, it, expect, vi, afterEach } from 'vitest';
import { missionId, missionsApi } from './missions-api';

afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('missionId', () => {
  // Same cases as TestMissionId in backend/tests/test_missions.py
  it.each([
    ['IFT 14', 'ift-14'],
    ['Flight 12', 'flight-12'],
    ['  IFT-14  ', 'ift-14'],
    ['Ship 33 / Booster 15!', 'ship-33-booster-15'],
    ['!!!', ''],
  ])('%s -> %s', (name, expected) => {
    expect(missionId(name)).toBe(expected);
  });

  it('is capped at 64 characters', () => {
    expect(missionId('x'.repeat(200))).toHaveLength(64);
  });
});

describe('missionsApi.list', () => {
  it('lists saved missions, then recorded flights without a file', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/api/missions'
          ? jsonResponse([{ id: 'ift-14', mission: 'IFT 14', ship: 'S33', booster: 'B15', updated_at: null }])
          : jsonResponse([
              { name: 'IFT 13', filename: 'ift-13' },
              { name: 'IFT 14', filename: 'ift-14' },
            ])
      )
    );

    expect(await missionsApi.list()).toEqual([
      { id: 'ift-14', mission: 'IFT 14', ship: 'S33', booster: 'B15', saved: true },
      { id: 'ift-13', mission: 'IFT 13', ship: '', booster: '', saved: false },
    ]);
  });

  it('still lists recorded flights when the missions endpoint fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/api/missions' ? jsonResponse({}, 500) : jsonResponse([{ name: 'IFT 13', filename: 'ift-13' }])
      )
    );

    expect(await missionsApi.list()).toEqual([{ id: 'ift-13', mission: 'IFT 13', ship: '', booster: '', saved: false }]);
  });
});

describe('missionsApi.save / load', () => {
  it('PUTs to the file named after the mission', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ id: 'ift-15' }));
    vi.stubGlobal('fetch', fetchMock);

    await missionsApi.save({ mission: 'IFT 15', ship: 'S35', booster: '', starship_engines: {} });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/missions/ift-15');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body as string)).toEqual({ mission: 'IFT 15', ship: 'S35', booster: '', starship_engines: {} });
  });

  it('load gives null for a mission without a file', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'nope' }, 404)));
    expect(await missionsApi.load('ift-99')).toBeNull();
  });
});
