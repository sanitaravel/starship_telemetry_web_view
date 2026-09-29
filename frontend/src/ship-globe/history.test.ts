import { describe, it, expect, vi } from 'vitest';
import { fetchHistoricalTrajectories, toCoordinates, HISTORY_COLORS } from './history';

function point(latitude: number, longitude: number, altitude = 1000) {
  return { gps_time: 1, mission_time: 1, latitude, longitude, altitude };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

describe('toCoordinates', () => {
  it('returns [longitude, latitude] pairs in order', () => {
    expect(toCoordinates([point(25.99, -97.15), point(-17.6, 106.72)])).toEqual([
      [-97.15, 25.99],
      [106.72, -17.6],
    ]);
  });

  it('skips below-ground and malformed points', () => {
    expect(
      toCoordinates([point(1, 1, -4), { latitude: 'x', longitude: 2 }, null, point(3, 3)]),
    ).toEqual([[3, 3]]);
  });

  it('returns an empty list for non-array input', () => {
    expect(toCoordinates({ detail: 'nope' })).toEqual([]);
  });
});

describe('fetchHistoricalTrajectories', () => {
  it('loads every listed trajectory with a distinct colour', async () => {
    const fetchFn = vi.fn(async (url: string) => {
      if (url === '/api/trajectories') {
        return jsonResponse([
          { name: 'ship39', number: 39, label: 'Ship 39' },
          { name: 'ship41', number: 41, label: 'Ship 41' },
        ]);
      }
      return jsonResponse([point(0, 0), point(1, 1)]);
    });

    const result = await fetchHistoricalTrajectories(fetchFn as unknown as typeof fetch);

    expect(result.map((t) => [t.name, t.color])).toEqual([
      ['ship39', HISTORY_COLORS[0]],
      ['ship41', HISTORY_COLORS[1]],
    ]);
    expect(result[0].coordinates).toEqual([[0, 0], [1, 1]]);
    expect(fetchFn).toHaveBeenCalledWith('/api/trajectories/ship41');
  });

  it('leaves out trajectories that fail or are too short', async () => {
    const fetchFn = vi.fn(async (url: string) => {
      if (url === '/api/trajectories') {
        return jsonResponse([
          { name: 'ship1', number: 1, label: 'Ship 1' },
          { name: 'ship2', number: 2, label: 'Ship 2' },
          { name: 'ship3', number: 3, label: 'Ship 3' },
        ]);
      }
      if (url.endsWith('ship1')) return jsonResponse({ detail: 'missing' }, 404);
      if (url.endsWith('ship2')) return jsonResponse([point(0, 0)]);
      return jsonResponse([point(0, 0), point(1, 1)]);
    });

    const result = await fetchHistoricalTrajectories(fetchFn as unknown as typeof fetch);
    expect(result.map((t) => t.name)).toEqual(['ship3']);
  });

  it('returns nothing when the backend is unreachable', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('offline'));
    await expect(fetchHistoricalTrajectories(fetchFn)).resolves.toEqual([]);
  });
});
