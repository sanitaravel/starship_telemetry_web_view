/**
 * Sub-second mission elapsed time estimation.
 *
 * The on-screen MET clock only shows whole seconds, so every frame sampled
 * within the same second reads the same MET. Each telemetry record also
 * carries a frame-based timestamp (ms of video position), which has sub-second
 * resolution but drifts from the real clock (reported FPS vs actual FPS) and
 * restarts at 0 when the backend reconnects.
 *
 * MetClock combines the two: it anchors the frame timestamp to the MET clock
 * and extrapolates from that anchor, re-anchoring whenever the estimate falls
 * outside the window the clock allows, [met, met + 1).
 */

/**
 * Offset below the next whole second used when an estimate runs ahead of the
 * clock, so the re-anchored point still sorts before the next second's samples.
 */
export const MET_CLOCK_AHEAD_EPSILON_SECONDS = 0.001;

interface Anchor {
  /** Estimated MET (seconds from T-0) at the anchor frame. */
  met: number;
  /** Frame timestamp (ms) at the anchor frame. */
  timestampMs: number;
}

export class MetClock {
  private anchor: Anchor | null = null;

  /**
   * Estimate the sub-second MET of a sample.
   *
   * @param metSeconds Whole-second MET read from the clock (seconds from T-0).
   * @param timestampMs Frame timestamp of the sample, in milliseconds.
   * @returns Estimated MET in seconds, always within [metSeconds, metSeconds + 1).
   */
  estimate(metSeconds: number, timestampMs: number): number {
    if (this.anchor) {
      const x = this.anchor.met + (timestampMs - this.anchor.timestampMs) / 1000;
      if (x >= metSeconds && x < metSeconds + 1) {
        return x;
      }
      if (x >= metSeconds + 1 && x < metSeconds + 2) {
        // Slightly ahead of the clock: the frame timestamp runs fast. The clock
        // has not ticked yet, so the true time is just below the next second.
        return this.setAnchor(metSeconds + 1 - MET_CLOCK_AHEAD_EPSILON_SECONDS, timestampMs);
      }
    }
    // No anchor yet, the estimate lags the clock (which also tightens the
    // anchor to the moment the clock ticks), a reconnect reset the frame
    // timestamps, or the MET jumped.
    return this.setAnchor(metSeconds, timestampMs);
  }

  /** Forget the anchor, e.g. when a new session starts. */
  reset(): void {
    this.anchor = null;
  }

  private setAnchor(met: number, timestampMs: number): number {
    this.anchor = { met, timestampMs };
    return met;
  }
}
