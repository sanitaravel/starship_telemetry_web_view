/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi } from 'vitest';
import { engineEventsPlugin, hitTestMarkers } from './engine-events-plugin';
import type { EngineMarker } from './engine-markers';

const AREA = { left: 50, right: 750, top: 20, bottom: 380 };

describe('hitTestMarkers', () => {
  it('hits a marker within the tolerance anywhere in the chart height', () => {
    expect(hitTestMarkers([100, 300], 104, 25, AREA)).toBe(0);
    expect(hitTestMarkers([100, 300], 296, 370, AREA)).toBe(1);
  });

  it('misses markers further than the tolerance', () => {
    expect(hitTestMarkers([100, 300], 107, 200, AREA)).toBeNull();
    expect(hitTestMarkers([], 100, 200, AREA)).toBeNull();
  });

  it('picks the nearest marker when several are in reach', () => {
    expect(hitTestMarkers([100, 104, 110], 105, 200, AREA)).toBe(1);
    expect(hitTestMarkers([100, 104], 101, 200, AREA)).toBe(0);
  });

  it('never hits outside the chart area', () => {
    expect(hitTestMarkers([100], 100, 10, AREA)).toBeNull();
    expect(hitTestMarkers([100], 100, 390, AREA)).toBeNull();
    expect(hitTestMarkers([52], 48, 200, AREA)).toBeNull();
  });

  it('skips markers off the visible range', () => {
    expect(hitTestMarkers([Number.NaN, 100], 100, 200, AREA)).toBe(1);
    expect(hitTestMarkers([Number.NaN], 100, 200, AREA)).toBeNull();
  });
});

describe('engineEventsPlugin hover tooltip', () => {
  /** A chart stub: x scale maps seconds 0..100 to px 50..750. */
  function fakeChart() {
    const container = document.createElement('div');
    const canvas = document.createElement('canvas');
    container.appendChild(canvas);
    document.body.appendChild(container);
    const tooltip = { getActiveElements: vi.fn(() => [{}]), setActiveElements: vi.fn() };
    const chart = {
      canvas,
      chartArea: AREA,
      scales: { x: { min: 0, max: 100, getPixelForValue: (v: number) => 50 + v * 7 } },
      tooltip,
    };
    return { chart, container, tooltip };
  }

  const markers: EngineMarker[] = [
    { x: 10, color: '#ff3b30', kind: 'engine_out', tooltipLines: ['Engine out: Super Heavy E17'] },
    { x: 10, color: '#00bcd4', kind: 'engine_out', tooltipLines: ['IFT-12 — Engine out: Super Heavy E5'] },
    { x: 60, color: '#ffb020', kind: 'failed_to_light', tooltipLines: ['Failed to light: Super Heavy E7'] },
  ];

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fire = (chart: unknown, type: string, x: number, y: number): any => {
    const args = { event: { type, x, y }, replay: false, cancelable: false, inChartArea: true } as never;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (engineEventsPlugin.afterEvent as any)(chart, args, { markers });
    return args;
  };

  it('shows every marker at the hovered spot and hides the regular tooltip', () => {
    const { chart, container, tooltip } = fakeChart();
    const args = fire(chart, 'mousemove', 122, 200);

    const el = container.querySelector('.time-series-graphs__engine-tooltip') as HTMLElement;
    expect(el).not.toBeNull();
    expect(el.getAttribute('role')).toBe('tooltip');
    expect(el.hidden).toBe(false);
    expect([...el.children].map((c) => c.textContent)).toEqual([
      'Engine out: Super Heavy E17',
      'IFT-12 — Engine out: Super Heavy E5',
    ]);
    expect(tooltip.setActiveElements).toHaveBeenCalledWith([], { x: 122, y: 200 });
    expect(args.changed).toBe(true);
  });

  it('hides when the mouse leaves the marker or the chart', () => {
    const { chart, container } = fakeChart();
    fire(chart, 'mousemove', 470, 200);
    const el = container.querySelector('.time-series-graphs__engine-tooltip') as HTMLElement;
    expect(el.textContent).toBe('Failed to light: Super Heavy E7');

    fire(chart, 'mousemove', 300, 200);
    expect(el.hidden).toBe(true);
    fire(chart, 'mousemove', 470, 200);
    fire(chart, 'mouseout', 470, 200);
    expect(el.hidden).toBe(true);
  });

  it('creates no tooltip until a marker is hovered', () => {
    const { chart, container } = fakeChart();
    fire(chart, 'mousemove', 300, 200);
    expect(container.querySelector('.time-series-graphs__engine-tooltip')).toBeNull();
  });
});
