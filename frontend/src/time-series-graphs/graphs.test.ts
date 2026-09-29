/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { TimeSeriesStore } from '../state';

vi.mock('chart.js/auto', () => {
  class Chart {
    static instances: Chart[] = [];
    data: { datasets: unknown[] };
    width = 800;
    height = 400;
    currentDevicePixelRatio = 2;
    // A zoomed-in live chart: these ranges should carry over to the export
    scales = { x: { min: 30, max: 90 }, y: { min: 0, max: 5000 } };
    update = vi.fn();
    destroy = vi.fn();
    resetZoom = vi.fn();
    static register = vi.fn();
    constructor(public canvas: HTMLCanvasElement, public config: any) {
      this.data = config.data;
      Chart.instances.push(this);
    }
  }
  return { Chart };
});

vi.mock('../previous-flights', () => ({
  fetchPreviousFlightList: vi.fn(),
  fetchPreviousFlightData: vi.fn(),
}));

vi.mock('./export-png', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./export-png')>()),
  saveCanvasAsPng: vi.fn(),
}));

import { Chart } from 'chart.js/auto';
import { TimeSeriesGraphs } from './graphs';
import { fetchPreviousFlightList, fetchPreviousFlightData } from '../previous-flights';
import { EXPORT_THEMES, saveCanvasAsPng } from './export-png';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const chartInstances = (Chart as any).instances as any[];

const EMPTY: TimeSeriesStore = {
  speedSuperHeavy: [],
  speedStarship: [],
  altitudeSuperHeavy: [],
  altitudeStarship: [],
};

/** A promise whose resolution is controlled by the test. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

async function setup(): Promise<HTMLElement> {
  vi.mocked(fetchPreviousFlightList).mockResolvedValue([
    { filename: 'flight-a.json', name: 'Flight A' },
    { filename: 'flight-b.json', name: 'Flight B' },
  ] as never);
  const container = document.createElement('div');
  document.body.appendChild(container);
  const stateManager = { subscribe: vi.fn() };
  new TimeSeriesGraphs(container, stateManager as never);
  await flush();
  return container;
}

function overlay(container: HTMLElement): HTMLElement {
  return container.querySelector('.time-series-graphs__loading') as HTMLElement;
}

function toggle(container: HTMLElement, filename: string): void {
  const box = container.querySelector(`input[value="${filename}"]`) as HTMLInputElement;
  box.checked = !box.checked;
  box.dispatchEvent(new Event('change'));
}

describe('TimeSeriesGraphs comparison loading overlay', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    vi.mocked(fetchPreviousFlightData).mockReset();
  });

  it('is hidden before any comparison is selected', async () => {
    const container = await setup();
    expect(overlay(container).hidden).toBe(true);
  });

  it('shows while a comparison loads and hides once it has loaded', async () => {
    const container = await setup();
    const load = deferred<TimeSeriesStore | null>();
    vi.mocked(fetchPreviousFlightData).mockReturnValue(load.promise);

    toggle(container, 'flight-a.json');
    await flush();
    expect(overlay(container).hidden).toBe(false);

    load.resolve(EMPTY);
    await flush();
    expect(overlay(container).hidden).toBe(true);
  });

  it('stays visible until every selected comparison has loaded', async () => {
    const container = await setup();
    const loadA = deferred<TimeSeriesStore | null>();
    const loadB = deferred<TimeSeriesStore | null>();
    vi.mocked(fetchPreviousFlightData).mockImplementation((f) =>
      f === 'flight-a.json' ? loadA.promise : loadB.promise,
    );

    toggle(container, 'flight-a.json');
    toggle(container, 'flight-b.json');

    loadA.resolve(EMPTY);
    await flush();
    expect(overlay(container).hidden).toBe(false);

    loadB.resolve(EMPTY);
    await flush();
    expect(overlay(container).hidden).toBe(true);
  });

  it('hides after a failed load', async () => {
    const container = await setup();
    const load = deferred<TimeSeriesStore | null>();
    vi.mocked(fetchPreviousFlightData).mockReturnValue(load.promise);

    toggle(container, 'flight-a.json');
    load.reject(new Error('network down'));
    await flush();
    expect(overlay(container).hidden).toBe(true);
  });

  it('does not fetch a comparison again while it is still loading', async () => {
    const container = await setup();
    const loadA = deferred<TimeSeriesStore | null>();
    const loadB = deferred<TimeSeriesStore | null>();
    vi.mocked(fetchPreviousFlightData).mockImplementation((f) =>
      f === 'flight-a.json' ? loadA.promise : loadB.promise,
    );

    toggle(container, 'flight-a.json');
    toggle(container, 'flight-b.json');
    loadA.resolve(EMPTY);
    loadB.resolve(EMPTY);
    await flush();

    const calls = vi.mocked(fetchPreviousFlightData).mock.calls.map((c) => c[0]);
    expect(calls).toEqual(['flight-a.json', 'flight-b.json']);
  });
});

describe('TimeSeriesGraphs PNG export', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    localStorage.clear();
    chartInstances.length = 0;
    vi.mocked(saveCanvasAsPng).mockReset();
  });

  function openMenuAndDownload(container: HTMLElement, fontSize?: string, background?: string): void {
    (container.querySelector('.time-series-graphs__save-btn') as HTMLButtonElement).click();
    if (fontSize) {
      const font = container.querySelector('.time-series-graphs__export-font') as HTMLSelectElement;
      font.value = fontSize;
      font.dispatchEvent(new Event('change'));
    }
    if (background) {
      const radio = container.querySelector(`input[value="${background}"]`) as HTMLInputElement;
      radio.checked = true;
      radio.dispatchEvent(new Event('change'));
    }
    (container.querySelector('.time-series-graphs__export-download') as HTMLButtonElement).click();
  }

  it('renders the export off-screen with the chosen font size and background', async () => {
    const container = await setup();
    const select = container.querySelector('.time-series-graphs__select') as HTMLSelectElement;
    select.value = 'accelerationStarship';
    select.dispatchEvent(new Event('change'));
    const live = chartInstances[chartInstances.length - 1];
    const liveConfigBefore = JSON.stringify(live.config.options.scales);

    openMenuAndDownload(container, '20', 'white');

    // A separate chart was built for the export, sized and zoomed like the live one
    const exported = chartInstances[chartInstances.length - 1];
    expect(exported).not.toBe(live);
    expect(exported.canvas).not.toBe(container.querySelector('#chart-main'));
    expect([exported.canvas.width, exported.canvas.height]).toEqual([800, 400]);
    const { options } = exported.config;
    expect(options.responsive).toBe(false);
    expect(options.devicePixelRatio).toBe(2);
    expect([options.scales.x.min, options.scales.x.max]).toEqual([30, 90]);
    expect([options.scales.y.min, options.scales.y.max]).toEqual([0, 5000]);

    // ...styled with the chosen options
    expect(options.scales.x.ticks.font.size).toBe(20);
    expect(options.scales.x.title.font.size).toBe(21);
    expect(options.plugins.legend.labels.font.size).toBe(21);
    expect(options.scales.y.ticks.color).toBe(EXPORT_THEMES.white.text);
    expect(options.scales.y.grid.color).toBe(EXPORT_THEMES.white.grid);

    // ...saved with the matching background and watermark, then destroyed
    expect(saveCanvasAsPng).toHaveBeenCalledTimes(1);
    const [canvas, filename, style, ratio] = vi.mocked(saveCanvasAsPng).mock.calls[0];
    expect(canvas).toBe(exported.canvas);
    expect(filename).toMatch(/^starship-accelerationStarship-.*\.png$/);
    expect(style).toEqual({
      fill: EXPORT_THEMES.white.fill,
      watermarkColor: EXPORT_THEMES.white.watermark,
      watermarkFontPx: 24,
    });
    expect(ratio).toBe(2);
    expect(exported.destroy).toHaveBeenCalled();

    // The on-screen chart is untouched
    expect(live.destroy).not.toHaveBeenCalled();
    expect(JSON.stringify(live.config.options.scales)).toBe(liveConfigBefore);
    expect(live.config.options.scales.x.ticks.font.size).toBe(10);
  });

  it('defaults to the on-screen look: 10 px on the dark background', async () => {
    const container = await setup();
    openMenuAndDownload(container);

    const exported = chartInstances[chartInstances.length - 1];
    expect(exported.config.options.scales.x.ticks.font.size).toBe(10);
    expect(exported.config.options.scales.x.ticks.color).toBe(EXPORT_THEMES.dark.text);
    const [, , style] = vi.mocked(saveCanvasAsPng).mock.calls[0];
    expect(style.fill).toBe(EXPORT_THEMES.dark.fill);
  });
});
