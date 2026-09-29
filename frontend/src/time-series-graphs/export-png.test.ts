import { describe, it, expect } from 'vitest';
import { chartFilename } from './export-png';

describe('chartFilename', () => {
  it('names the file after the series and the local time, zero-padded', () => {
    const when = new Date(2026, 0, 5, 7, 3, 9); // local time
    expect(chartFilename('accelerationStarship', when)).toBe(
      'starship-accelerationStarship-2026-01-05_07-03-09.png',
    );
  });

  it('produces a filename safe on Windows (no colons)', () => {
    expect(chartFilename('speedSuperHeavy')).not.toMatch(/[:\\/*?"<>|]/);
  });
});
