import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { MetClock, MET_CLOCK_AHEAD_EPSILON_SECONDS } from './met-clock';

describe('MetClock', () => {
  it('anchors the first sample to its whole-second MET', () => {
    const clock = new MetClock();
    expect(clock.estimate(0, 5000)).toBe(0);
  });

  it('spreads samples within a second using the frame timestamp', () => {
    const clock = new MetClock();
    expect(clock.estimate(0, 5000)).toBe(0);
    expect(clock.estimate(0, 5333)).toBeCloseTo(0.333, 10);
    expect(clock.estimate(0, 5667)).toBeCloseTo(0.667, 10);
    expect(clock.estimate(1, 6000)).toBeCloseTo(1, 10);
    expect(clock.estimate(1, 6333)).toBeCloseTo(1.333, 10);
  });

  it('re-anchors when the estimate lags the clock', () => {
    const clock = new MetClock();
    // First sample landed late in second 0, so the anchor is ~0.5 s behind
    clock.estimate(0, 5000);
    // Clock ticked to 1 while the estimate is only 0.6
    expect(clock.estimate(1, 5600)).toBe(1);
    expect(clock.estimate(1, 5900)).toBeCloseTo(1.3, 10);
  });

  it('pulls an estimate that runs ahead of the clock back below the next second', () => {
    const clock = new MetClock();
    clock.estimate(0, 0);
    // 1.2 s of frame time passed but the clock still reads 0
    expect(clock.estimate(0, 1200)).toBe(1 - MET_CLOCK_AHEAD_EPSILON_SECONDS);
    // The new anchor carries on from there
    expect(clock.estimate(1, 1300)).toBeCloseTo(1.099, 10);
  });

  it('re-anchors after a reconnect resets the frame timestamps', () => {
    const clock = new MetClock();
    clock.estimate(100, 400_000);
    expect(clock.estimate(130, 0)).toBe(130);
    expect(clock.estimate(130, 500)).toBeCloseTo(130.5, 10);
  });

  it('re-anchors when the MET jumps far ahead of the frame timestamp', () => {
    const clock = new MetClock();
    clock.estimate(10, 0);
    expect(clock.estimate(5, 500)).toBe(5);
  });

  it('starts over after reset', () => {
    const clock = new MetClock();
    clock.estimate(0, 0);
    clock.reset();
    expect(clock.estimate(3, 200)).toBe(3);
  });

  it('always returns a time within the second the clock shows', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            met: fc.integer({ min: -60, max: 4000 }),
            timestampMs: fc.integer({ min: 0, max: 5_000_000 }),
          }),
          { minLength: 1, maxLength: 50 },
        ),
        (samples) => {
          const clock = new MetClock();
          for (const { met, timestampMs } of samples) {
            const x = clock.estimate(met, timestampMs);
            expect(x).toBeGreaterThanOrEqual(met);
            expect(x).toBeLessThan(met + 1);
          }
        },
      ),
    );
  });

  it('is monotonic for an in-order stream whose frame clock drifts', () => {
    // Frame clock runs 0.5% fast or slow against real time; 3 samples/s.
    fc.assert(
      fc.property(
        fc.double({ min: 0.995, max: 1.005, noNaN: true }),
        fc.double({ min: 0, max: 0.99, noNaN: true }),
        (rate, phase) => {
          const clock = new MetClock();
          let prev = -Infinity;
          let monotonic = true;
          for (let k = 0; k < 3 * 600; k++) {
            const t = phase + k / 3;
            const x = clock.estimate(Math.floor(t), t * rate * 1000);
            if (x < prev) monotonic = false;
            prev = x;
          }
          return monotonic;
        },
      ),
      { numRuns: 50 },
    );
  });
});
