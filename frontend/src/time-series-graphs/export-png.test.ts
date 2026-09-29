/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { chartFilename, EXPORT_THEMES, renderSnapshot, WATERMARK_TEXT, watermarkFontPx } from './export-png';

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
    return source;
  }

  const DARK_STYLE = {
    fill: EXPORT_THEMES.dark.fill,
    watermarkColor: EXPORT_THEMES.dark.watermark,
    watermarkFontPx: watermarkFontPx(10),
  };

  it('stamps the watermark in the bottom-right corner, scaled to the pixel ratio', () => {
    const { ctx } = stubContext();
    const out = renderSnapshot(hiDpiSource(), DARK_STYLE, 2);

    expect(out?.width).toBe(1600);
    expect(out?.height).toBe(800);
    expect(WATERMARK_TEXT).toBe('@sanitaravel');
    expect(ctx.fillText).toHaveBeenCalledWith(WATERMARK_TEXT, 1580, 780);
    expect(ctx.textAlign).toBe('right');
    expect(ctx.textBaseline).toBe('bottom');
    expect(ctx.font).toMatch(/^24px /);
    expect(ctx.fillStyle).toBe(EXPORT_THEMES.dark.watermark);
  });

  it('draws the background, then the chart, then the watermark on top', () => {
    const { calls } = stubContext();
    renderSnapshot(hiDpiSource(), DARK_STYLE, 2);
    expect(calls).toEqual(['fillRect', 'drawImage', 'fillText']);
  });

  it('skips the background fill for a transparent export', () => {
    const { calls } = stubContext();
    renderSnapshot(hiDpiSource(), { ...DARK_STYLE, fill: EXPORT_THEMES.transparent.fill }, 2);
    expect(calls).toEqual(['drawImage', 'fillText']);
  });

  it('scales the watermark with the chosen font size', () => {
    const { ctx } = stubContext();
    renderSnapshot(hiDpiSource(), { ...DARK_STYLE, watermarkFontPx: watermarkFontPx(20) }, 2);
    expect(ctx.font).toMatch(/^48px /);
  });
});

describe('EXPORT_THEMES', () => {
  it('fills dark and white exports and leaves transparent ones unfilled', () => {
    expect(EXPORT_THEMES.dark.fill).toBe('#333333');
    expect(EXPORT_THEMES.white.fill).toBe('#FFFFFF');
    expect(EXPORT_THEMES.transparent.fill).toBeNull();
  });
});
