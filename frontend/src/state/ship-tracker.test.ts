import { describe, it, expect, vi } from 'vitest';
import { ShipTracker } from './ship-tracker';
import { SHIP_TRACKER_URL } from '../api/ship-tracker-feed';
import type { ShipPosition } from '../core/ship-track';

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

  it('stamps stored samples with the poll time', () => {
    const tracker = new ShipTracker();
    tracker.ingest(new Map([['ship40', position(10)]]), 1_700_000_000_000);
    tracker.ingest(new Map([['ship40', position(10)]]), 1_700_000_030_000);
    expect(tracker.getTracks()[0].positions.map((p) => p.polledAt)).toEqual([1_700_000_000_000]);
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
    expect(fetchFn).toHaveBeenCalledWith(
      expect.stringMatching(/^https:\/\/example\.test\/feed\.json\?\d{13}$/),
      { cache: 'no-store' },
    );
  });

  it('requests a distinct cache-busting URL on every poll', async () => {
    const fetchFn = vi.fn(async (_url: string, _init?: RequestInit) => new Response('{}'));
    const now = vi.spyOn(Date, 'now').mockReturnValueOnce(1790689165010).mockReturnValueOnce(1790689195010);
    const tracker = new ShipTracker(SHIP_TRACKER_URL, 1000, fetchFn as unknown as typeof fetch);

    await tracker.poll();
    await tracker.poll();

    expect(fetchFn.mock.calls.map(([url]) => url)).toEqual([
      `${SHIP_TRACKER_URL}?1790689165010`,
      `${SHIP_TRACKER_URL}?1790689195010`,
    ]);
    now.mockRestore();
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

  it('logs a failed request under the ShipTracker logger', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      const tracker = new ShipTracker('u', 1000, vi.fn().mockRejectedValue(new Error('offline')));
      await tracker.poll();
      expect(warn).toHaveBeenCalledTimes(1);
      expect(warn).toHaveBeenCalledWith(
        expect.stringMatching(/\[ShipTracker\] Tracker feed request failed .*Error: offline/),
      );
    } finally {
      warn.mockRestore();
    }
  });

  it('does not notify listeners or store anything after an HTTP error', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      const tracker = new ShipTracker('u', 1000, vi.fn(async () => new Response('', { status: 500 })));
      const listener = vi.fn();
      tracker.subscribe(listener);
      listener.mockClear();
      await tracker.poll();
      expect(listener).not.toHaveBeenCalled();
      expect(warn).toHaveBeenCalledWith(expect.stringContaining('[ShipTracker] Tracker feed returned HTTP 500'));
    } finally {
      warn.mockRestore();
    }
  });

  it('polls on start and then every interval until stopped; start is idempotent', async () => {
    vi.useFakeTimers();
    try {
      const fetchFn = vi.fn(async () => new Response('{}'));
      const tracker = new ShipTracker('u', 1000, fetchFn);
      tracker.start();
      tracker.start();
      expect(fetchFn).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(999);
      expect(fetchFn).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(fetchFn).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(2000);
      expect(fetchFn).toHaveBeenCalledTimes(4);
      tracker.stop();
      await vi.advanceTimersByTimeAsync(5000);
      expect(fetchFn).toHaveBeenCalledTimes(4);
      tracker.start();
      expect(fetchFn).toHaveBeenCalledTimes(5);
      tracker.stop();
    } finally {
      vi.useRealTimers();
    }
  });
});
