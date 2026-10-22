import { describe, it, expect, vi } from 'vitest';
import fc from 'fast-check';
import { fetchTrackerFeed, parseTrackerPayload, shipNumber, withCacheBuster } from './ship-tracker-feed';

function current(overrides: Record<string, unknown> = {}) {
  return {
    gps_time: 1469206400.5,
    mission_time: 1200,
    altitude: 150_000,
    speed: 7_000,
    latitude: 20.1,
    longitude: -80.5,
    r_ecef: [1, 2, 3],
    ...overrides,
  };
}

describe('parseTrackerPayload', () => {
  it('reads the current sample of each shipN entry and ignores metadata and trajectory', () => {
    const result = parseTrackerPayload({
      ship40: { current: current(), trajectory: [{ altitude: 1 }] },
      metadata: { generation_time: 1 },
    });
    expect([...result.keys()]).toEqual(['ship40']);
    expect(result.get('ship40')).toEqual({
      gpsTime: 1469206400.5,
      missionTime: 1200,
      altitude: 150_000,
      speed: 7_000,
      latitude: 20.1,
      longitude: -80.5,
    });
  });

  it('drops samples with negative altitude', () => {
    const result = parseTrackerPayload({
      ship40: { current: current({ altitude: -24.6 }) },
      ship41: { current: current({ altitude: 0 }) },
    });
    expect([...result.keys()]).toEqual(['ship41']);
  });

  it('skips non-ship keys and malformed entries', () => {
    const result = parseTrackerPayload({
      booster15: { current: current() },
      shipX: { current: current() },
      ship1: null,
      ship2: { current: current({ latitude: 'n/a' }) },
      ship3: {},
    });
    expect(result.size).toBe(0);
  });

  it('returns an empty map for non-object payloads', () => {
    expect(parseTrackerPayload(null).size).toBe(0);
    expect(parseTrackerPayload('oops').size).toBe(0);
  });

  it('never keeps a sample with altitude below 0 (property)', () => {
    fc.assert(
      fc.property(
        fc.dictionary(
          fc.integer({ min: 0, max: 999 }).map((n) => `ship${n}`),
          fc.double({ min: -1e7, max: 1e7, noNaN: true }),
        ),
        (altitudes) => {
          const payload = Object.fromEntries(
            Object.entries(altitudes).map(([k, alt]) => [k, { current: current({ altitude: alt }) }]),
          );
          const result = parseTrackerPayload(payload);
          for (const [key, alt] of Object.entries(altitudes)) {
            expect(result.has(key)).toBe(alt >= 0);
          }
        },
      ),
    );
  });
});

describe('withCacheBuster', () => {
  it('adds the timestamp as an extra parameter when the URL already has a query', () => {
    expect(withCacheBuster('https://example.test/feed.json?v=2', 42)).toBe('https://example.test/feed.json?v=2&42');
    expect(withCacheBuster('https://example.test/feed.json', 42)).toBe('https://example.test/feed.json?42');
  });
});

describe('shipNumber', () => {
  it('reads N from a ship<N> key (property)', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 1_000_000 }), (n) => {
        expect(shipNumber(`ship${n}`)).toBe(n);
      }),
    );
  });

  it('returns 0 for keys of another form', () => {
    for (const key of ['booster15', 'shipX', 'ship', 'ship40a', 'xship40', '']) {
      expect(shipNumber(key)).toBe(0);
    }
  });
});

describe('fetchTrackerFeed', () => {
  it('requests the cache-busted URL with no-store and parses the body', async () => {
    const fetchFn = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response(JSON.stringify({ ship40: { current: current() }, metadata: {} })),
    );
    const result = await fetchTrackerFeed('https://example.test/feed.json', 42, fetchFn as unknown as typeof fetch);
    expect(fetchFn).toHaveBeenCalledWith('https://example.test/feed.json?42', { cache: 'no-store' });
    expect(result && [...result.keys()]).toEqual(['ship40']);
  });

  it('returns null and logs on an HTTP error', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      const fetchFn = vi.fn(async () => new Response('', { status: 503 }));
      await expect(fetchTrackerFeed('u', 1, fetchFn)).resolves.toBeNull();
      expect(warn).toHaveBeenCalledTimes(1);
      expect(warn).toHaveBeenCalledWith(expect.stringContaining('[ShipTracker] Tracker feed returned HTTP 503'));
    } finally {
      warn.mockRestore();
    }
  });

  it('throws network and JSON errors to the caller', async () => {
    const offline = vi.fn().mockRejectedValue(new Error('offline'));
    await expect(fetchTrackerFeed('u', 1, offline)).rejects.toThrow('offline');
    const badJson = vi.fn(async () => new Response('not json'));
    await expect(fetchTrackerFeed('u', 1, badJson)).rejects.toThrow();
  });
});
