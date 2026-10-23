// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { fitCanvas } from './interaction';
import { MAX_GLOBE_SIZE } from './map-data';

/** A wrapper whose layout size jsdom would otherwise report as 0. */
function wrapOf(width: number, height: number): HTMLElement {
  const wrap = document.createElement('div');
  Object.defineProperty(wrap, 'clientWidth', { value: width });
  Object.defineProperty(wrap, 'clientHeight', { value: height });
  return wrap;
}

describe('fitCanvas', () => {
  it('is square from the column width on the dashboard, capped', () => {
    const canvas = document.createElement('canvas');
    expect(fitCanvas(canvas, wrapOf(500, 50), { width: 0, height: 0 })).toEqual({ width: 500, height: 500 });
    expect(fitCanvas(canvas, wrapOf(900, 50), { width: 0, height: 0 })).toEqual({ width: 900, height: MAX_GLOBE_SIZE });
  });

  it('takes the wrapper height too with fill (overlays), so the globe fits the page', () => {
    const canvas = document.createElement('canvas');
    expect(fitCanvas(canvas, wrapOf(382, 366), { width: 0, height: 0 }, true)).toEqual({ width: 382, height: 366 });
    expect(canvas.style.height).toBe('366px');
    expect(fitCanvas(canvas, wrapOf(382, 0), { width: 0, height: 0 }, true)).toBeNull();
  });
});
