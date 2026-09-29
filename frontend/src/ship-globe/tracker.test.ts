import { describe, it, expect, vi } from 'vitest';
import fc from 'fast-check';
import { parseTrackerPayload, ShipTracker, type ShipPosition } from './tracker';

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

function position(gpsTime: number, altitude = 100_000): ShipPosition {
  return { gpsTime, missionTime: 0, altitude, speed: 0, latitude: 0, longitude: 0 };
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

describe('ShipTracker', () => {
  it('accumulates new samples and ignores repeats', () => {
    const tracker = new ShipTracker();
    const listener = vi.fn();
    tracker.subscribe(listener);
    listener.mockClear();

    tracker.ingest(new Map([['ship40', position(10)]]));
    tracker.ingest(new Map([['ship40', position(10)]]));
    tracker.ingest(new Map([['ship40', position(20)]]));

    expect(listener).toHaveBeenCalledTimes(2);
    const [track] = tracker.getTracks();
    expect(track.id).toBe('ship40');
    expect(track.number).toBe(40);
    expect(track.positions.map((p) => p.gpsTime)).toEqual([10, 20]);
  });

  it('sorts tracks by ship number', () => {
    const tracker = new ShipTracker();
    tracker.ingest(new Map([['ship41', position(1)], ['ship9', position(1)]]));
    expect(tracker.getTracks().map((t) => t.number)).toEqual([9, 41]);
  });

  it('polls the feed and stores only above-ground samples', async () => {
    const payloads = [
      { ship40: { current: current({ gps_time: 1, altitude: -5 }) } },
      { ship40: { current: current({ gps_time: 2, altitude: 500 }) } },
    ];
    const fetchFn = vi.fn(async () => new Response(JSON.stringify(payloads.shift())));
    const tracker = new ShipTracker('https://example.test/feed.json', 1000, fetchFn);

    await tracker.poll();
    expect(tracker.getTracks()).toEqual([]);

    await tracker.poll();
    expect(tracker.getTracks()[0].positions.map((p) => p.altitude)).toEqual([500]);
    expect(fetchFn).toHaveBeenCalledWith('https://example.test/feed.json', { cache: 'no-cache' });
  });

  it('survives fetch failures and HTTP errors', async () => {
    const fetchFn = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(new Response('', { status: 503 }));
    const tracker = new ShipTracker('u', 1000, fetchFn);
    await expect(tracker.poll()).resolves.toBeUndefined();
    await expect(tracker.poll()).resolves.toBeUndefined();
    expect(tracker.getTracks()).toEqual([]);
  });
});
