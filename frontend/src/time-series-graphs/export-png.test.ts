/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { chartFilename, renderSnapshot, WATERMARK_TEXT } from './export-png';

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

describe('renderSnapshot', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  /** Stub the 2D context (jsdom has none) and record the drawing calls in order. */
  function stubContext() {
    const calls: string[] = [];
    const ctx = {
      font: '',
      fillStyle: '',
      textAlign: '',
      textBaseline: '',
      fillRect: vi.fn(() => calls.push('fillRect')),
      drawImage: vi.fn(() => calls.push('drawImage')),
      fillText: vi.fn(() => calls.push('fillText')),
    };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as never);
    return { ctx, calls };
  }

  /** A 2x device-pixel-ratio chart canvas: 800x400 CSS px, 1600x800 backing. */
  function hiDpiSource(): HTMLCanvasElement {
    const source = document.createElement('canvas');
    source.width = 1600;
    source.height = 800;
    Object.defineProperty(source, 'clientWidth', { value: 800 });
    return source;
  }

  it('stamps the watermark in the bottom-right corner, scaled to the pixel ratio', () => {
    const { ctx } = stubContext();
    const out = renderSnapshot(hiDpiSource(), '#333333');

    expect(out?.width).toBe(1600);
    expect(out?.height).toBe(800);
    expect(WATERMARK_TEXT).toBe('@sanitaravel');
    expect(ctx.fillText).toHaveBeenCalledWith(WATERMARK_TEXT, 1580, 780);
    expect(ctx.textAlign).toBe('right');
    expect(ctx.textBaseline).toBe('bottom');
    expect(ctx.font).toMatch(/^24px /);
  });

  it('draws the background, then the chart, then the watermark on top', () => {
    const { calls } = stubContext();
    renderSnapshot(hiDpiSource(), '#333333');
    expect(calls).toEqual(['fillRect', 'drawImage', 'fillText']);
  });
});
