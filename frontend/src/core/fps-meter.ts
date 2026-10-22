/**
 * Frontend FPS meter.
 *
 * Measures actual frame processing rate from received telemetry/frame messages
 * using a sliding window approach. More accurate than the backend value because
 * it reflects the real arrival rate the user observes.
 */
export class FPSMeter {
  /** Timestamps (ms) of recent frame arrivals */
  private timestamps: number[] = [];
  /** Window size in milliseconds (default 5 seconds) */
  private readonly windowMs: number;
  /** Minimum frames needed for a valid FPS reading */
  private readonly minSamples: number;

  constructor(windowMs: number = 5000, minSamples: number = 2) {
    this.windowMs = windowMs;
    this.minSamples = minSamples;
  }

  /**
   * Record a frame arrival. Call this each time a telemetry record
   * or frame message is received from the backend.
   */
  tick(): void {
    const now = performance.now();
    this.timestamps.push(now);
    this.prune(now);
  }

  /**
   * Get the current FPS based on frame arrivals in the sliding window.
   * Returns 0 if not enough data points are available.
   */
  getFps(): number {
    const now = performance.now();
    this.prune(now);

    if (this.timestamps.length < this.minSamples) {
      return 0;
    }

    const oldest = this.timestamps[0];
    const elapsed = (now - oldest) / 1000; // seconds

    if (elapsed <= 0) {
      return 0;
    }

    // Number of frames in the window divided by the time span
    return this.timestamps.length / elapsed;
  }

  /**
   * Reset the meter (e.g., on pipeline stop/start).
   */
  reset(): void {
    this.timestamps = [];
  }

  /** Remove timestamps older than the window. */
  private prune(now: number): void {
    const cutoff = now - this.windowMs;
    while (this.timestamps.length > 0 && this.timestamps[0] < cutoff) {
      this.timestamps.shift();
    }
  }
}
