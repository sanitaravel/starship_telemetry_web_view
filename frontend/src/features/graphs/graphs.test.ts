/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { TimeSeriesStore } from '../../core/time-series';

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

vi.mock('../../api/previous-flights', () => ({
  fetchPreviousFlightList: vi.fn(),
  fetchPreviousFlightData: vi.fn(),
}));

vi.mock('./export/export-png', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./export/export-png')>()),
  saveCanvasAsPng: vi.fn(),
}));

import { Chart } from 'chart.js/auto';
import { TimeSeriesGraphs } from './graphs';
import { fetchPreviousFlightList, fetchPreviousFlightData, type PreviousFlightData } from '../../api/previous-flights';
import { EXPORT_THEMES, saveCanvasAsPng } from './export/export-png';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const chartInstances = (Chart as any).instances as any[];

const EMPTY: TimeSeriesStore = {
  speedSuperHeavy: [],
  speedStarship: [],
  altitudeSuperHeavy: [],
  altitudeStarship: [],
};

const EMPTY_FLIGHT: PreviousFlightData = { store: EMPTY, engineEvents: [], separationMetSeconds: null };

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
    const load = deferred<PreviousFlightData | null>();
    vi.mocked(fetchPreviousFlightData).mockReturnValue(load.promise);

    toggle(container, 'flight-a.json');
    await flush();
    expect(overlay(container).hidden).toBe(false);

    load.resolve(EMPTY_FLIGHT);
    await flush();
    expect(overlay(container).hidden).toBe(true);
  });

  it('stays visible until every selected comparison has loaded', async () => {
    const container = await setup();
    const loadA = deferred<PreviousFlightData | null>();
    const loadB = deferred<PreviousFlightData | null>();
    vi.mocked(fetchPreviousFlightData).mockImplementation((f) =>
      f === 'flight-a.json' ? loadA.promise : loadB.promise,
    );

    toggle(container, 'flight-a.json');
    toggle(container, 'flight-b.json');

    loadA.resolve(EMPTY_FLIGHT);
    await flush();
    expect(overlay(container).hidden).toBe(false);

    loadB.resolve(EMPTY_FLIGHT);
    await flush();
    expect(overlay(container).hidden).toBe(true);
  });

  it('hides after a failed load', async () => {
    const container = await setup();
    const load = deferred<PreviousFlightData | null>();
    vi.mocked(fetchPreviousFlightData).mockReturnValue(load.promise);

    toggle(container, 'flight-a.json');
    load.reject(new Error('network down'));
    await flush();
    expect(overlay(container).hidden).toBe(true);
  });

  it('does not fetch a comparison again while it is still loading', async () => {
    const container = await setup();
    const loadA = deferred<PreviousFlightData | null>();
    const loadB = deferred<PreviousFlightData | null>();
    vi.mocked(fetchPreviousFlightData).mockImplementation((f) =>
      f === 'flight-a.json' ? loadA.promise : loadB.promise,
    );

    toggle(container, 'flight-a.json');
    toggle(container, 'flight-b.json');
    loadA.resolve(EMPTY_FLIGHT);
    loadB.resolve(EMPTY_FLIGHT);
    await flush();

    const calls = vi.mocked(fetchPreviousFlightData).mock.calls.map((c) => c[0]);
    expect(calls).toEqual(['flight-a.json', 'flight-b.json']);
  });
});

describe('TimeSeriesGraphs update path', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    chartInstances.length = 0;
    vi.mocked(fetchPreviousFlightData).mockReset();
    // Run animation-frame callbacks immediately
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0);
      return 1;
    });
  });

  it('reuses a fetched comparison when it is selected again', async () => {
    const container = await setup();
    vi.mocked(fetchPreviousFlightData).mockResolvedValue(EMPTY_FLIGHT);

    toggle(container, 'flight-a.json');
    await flush();
    toggle(container, 'flight-a.json');
    toggle(container, 'flight-a.json');
    await flush();

    expect(fetchPreviousFlightData).toHaveBeenCalledTimes(1);
  });

  it('redraws only when the time series version changes', async () => {
    vi.mocked(fetchPreviousFlightList).mockResolvedValue([] as never);
    const container = document.createElement('div');
    document.body.appendChild(container);
    let handler: (state: unknown) => void = () => {};
    const stateManager = { subscribe: vi.fn((h) => (handler = h)) };
    new TimeSeriesGraphs(container, stateManager as never);
    await flush();
    const chart = chartInstances[chartInstances.length - 1];
    chart.update.mockClear();

    const timeSeries: TimeSeriesStore = {
      ...EMPTY,
      speedSuperHeavy: [{ missionElapsedTime: '+00:00:01', metSeconds: 1, timestamp: 0, value: 10, unit: 'km/h' }],
    };
    handler({ timeSeries, timeSeriesVersion: 1 });
    handler({ timeSeries, timeSeriesVersion: 1 }); // e.g. a frame message
    expect(chart.update).toHaveBeenCalledTimes(1);
    expect(chart.data.datasets[0].data).toEqual([{ x: 1, y: 10 }]);

    timeSeries.speedSuperHeavy.push({ missionElapsedTime: '+00:00:02', metSeconds: 2, timestamp: 0, value: 20, unit: 'km/h' });
    handler({ timeSeries, timeSeriesVersion: 2 });
    expect(chart.update).toHaveBeenCalledTimes(2);
    expect(chart.data.datasets[0].data).toEqual([{ x: 1, y: 10 }, { x: 2, y: 20 }]);
  });

  it('marks live and comparison engine events on the selected series', async () => {
    vi.mocked(fetchPreviousFlightList).mockResolvedValue([{ filename: 'flight-a.json', name: 'Flight A' }] as never);
    const container = document.createElement('div');
    document.body.appendChild(container);
    let handler: (state: unknown) => void = () => {};
    const stateManager = { subscribe: vi.fn((h) => (handler = h)) };
    new TimeSeriesGraphs(container, stateManager as never);
    await flush();
    const chart = chartInstances[chartInstances.length - 1];
    const markers = () => chart.config.options.plugins.engineEvents.markers as { x: number; color: string; tooltipLines: string[] }[];

    const sh = { kind: 'engine_out', vehicle: 'superheavy', engineIds: ['sh_e17'], rings: ['engines_outer'], metSeconds: 60, runningAfter: 32, expected: 33 };
    const ss = { ...sh, vehicle: 'starship', engineIds: ['ss_e2'], rings: ['atmo'], metSeconds: 300, runningAfter: 5, expected: 6 };
    const engineEvents: unknown[] = [];
    const state = { timeSeries: EMPTY, timeSeriesVersion: 0, engineEvents, engineEventsVersion: 0, separationMetSeconds: null };
    handler(state);
    expect(markers()).toEqual([]);

    // New events alone trigger a redraw
    chart.update.mockClear();
    engineEvents.push(sh, ss);
    handler({ ...state, engineEventsVersion: 1 });
    expect(chart.update).toHaveBeenCalledTimes(1);
    expect(markers().map((m) => m.x)).toEqual([60]);
    expect(markers()[0].tooltipLines[0]).toMatch(/^Engine out: Super Heavy E17/);

    // Comparison flight events in the flight's colour, prefixed with its name
    vi.mocked(fetchPreviousFlightData).mockResolvedValue({
      ...EMPTY_FLIGHT,
      engineEvents: [{ ...sh, metSeconds: 70 }, { ...sh, metSeconds: 200 }, ss],
      separationMetSeconds: 160,
    } as never);
    toggle(container, 'flight-a.json');
    await flush();
    expect(markers().map((m) => m.x)).toEqual([60, 70, 200]);
    expect(markers()[1].color).toBe('#e91e63');
    expect(markers()[1].tooltipLines[0]).toMatch(/^Flight A — Engine out/);

    // Starship series: Super Heavy events before separation, Starship events
    handler({ ...state, engineEventsVersion: 1, separationMetSeconds: 100 });
    const select = container.querySelector('.time-series-graphs__select') as HTMLSelectElement;
    select.value = 'accelerationStarship';
    select.dispatchEvent(new Event('change'));
    const rebuilt = chartInstances[chartInstances.length - 1];
    expect(rebuilt.config.options.plugins.engineEvents.markers.map((m: { x: number }) => m.x)).toEqual([60, 300, 70, 300]);
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

  it('draws the engine event markers in the export', async () => {
    vi.mocked(fetchPreviousFlightData).mockResolvedValue({
      ...EMPTY_FLIGHT,
      engineEvents: [{ kind: 'engine_out', vehicle: 'superheavy', engineIds: ['sh_e17'], rings: ['engines_outer'], metSeconds: 60, runningAfter: 32, expected: 33 }],
    });
    const container = await setup();
    toggle(container, 'flight-a.json');
    await flush();
    openMenuAndDownload(container);

    const exported = chartInstances[chartInstances.length - 1];
    expect(exported.config.options.plugins.engineEvents.markers).toEqual([
      expect.objectContaining({ x: 60, kind: 'engine_out' }),
    ]);
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

describe('TimeSeriesGraphs overlay mode and OBS snapshot', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    chartInstances.length = 0;
    vi.mocked(fetchPreviousFlightData).mockReset();
  });

  it('has an OBS button in the dashboard header', async () => {
    const container = await setup();
    expect(container.querySelector('.time-series-graphs__controls .obs-btn')?.textContent).toBe('OBS');
  });

  it('without chrome draws the canvas only, with zoom, pan and crosshair off', async () => {
    vi.mocked(fetchPreviousFlightList).mockResolvedValue([] as never);
    const container = document.createElement('div');
    document.body.appendChild(container);
    new TimeSeriesGraphs(container, { subscribe: vi.fn() } as never, { chrome: false, series: 'altitudeStarship' });
    await flush();

    expect(container.querySelectorAll('button, select, input, h2')).toHaveLength(0);
    expect(container.querySelector('.time-series-graphs__zoom-hint')).toBeNull();
    expect(container.querySelector('canvas#chart-main')).not.toBeNull();
    const { plugins } = chartInstances[chartInstances.length - 1].config.options;
    expect(plugins.zoom).toEqual({
      pan: { enabled: false },
      zoom: { wheel: { enabled: false }, pinch: { enabled: false }, drag: { enabled: false } },
    });
    expect(plugins.crosshair).toBe(false);
    expect(chartInstances[chartInstances.length - 1].config.options.scales.y.title.text).toBe('Altitude');
  });

  it('keeps zoom on and the crosshair registered on the dashboard', async () => {
    await setup();
    const { plugins } = chartInstances[chartInstances.length - 1].config.options;
    expect(plugins.zoom.pan.enabled).toBe(true);
    expect(plugins.crosshair).toBeUndefined();
  });

  it('without chrome loads the fixed comparisons by GET and draws them in the snapshot colours, in order', async () => {
    vi.mocked(fetchPreviousFlightList).mockResolvedValue([
      { filename: 'flight-a.json', name: 'Flight A' },
      { filename: 'flight-b.json', name: 'Flight B' },
    ] as never);
    const loadA = deferred<PreviousFlightData | null>();
    vi.mocked(fetchPreviousFlightData).mockImplementation((f) =>
      f === 'flight-a.json' ? loadA.promise : Promise.resolve(EMPTY_FLIGHT),
    );
    const getItem = vi.spyOn(Storage.prototype, 'getItem');
    const container = document.createElement('div');
    document.body.appendChild(container);
    const graphs = new TimeSeriesGraphs(container, { subscribe: vi.fn() } as never, {
      chrome: false,
      comparisons: [['flight-a.json', '#123456'], ['flight-b.json', '#abcdef']],
    });
    await flush();
    loadA.resolve(EMPTY_FLIGHT); // settles after B
    await flush();

    expect(vi.mocked(fetchPreviousFlightData).mock.calls.map((c) => c[0])).toEqual(['flight-a.json', 'flight-b.json']);
    const datasets = chartInstances[chartInstances.length - 1].data.datasets as { label: string; borderColor: string }[];
    // No live data yet, so only the comparisons
    expect(datasets.map((d) => [d.label, d.borderColor])).toEqual([
      ['Speed — Super Heavy (Flight A)', '#123456'],
      ['Speed — Super Heavy (Flight B)', '#abcdef'],
    ]);
    expect(graphs.overlaySnapshot()).toEqual({ v: 1, x: [['flight-a.json', '#123456'], ['flight-b.json', '#abcdef']] });
    expect(getItem).not.toHaveBeenCalledWith('flight-colors.telemetry');
    getItem.mockRestore();
  });

  it('warns when a fixed comparison is not among the previous flights', async () => {
    vi.mocked(fetchPreviousFlightList).mockResolvedValue([{ filename: 'ift-12', name: 'IFT 12' }] as never);
    vi.mocked(fetchPreviousFlightData).mockResolvedValue(null);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const container = document.createElement('div');
    document.body.appendChild(container);
    new TimeSeriesGraphs(container, { subscribe: vi.fn() } as never, {
      chrome: false,
      comparisons: [['ift-12', '#123456'], ['ift-12.json', '#abcdef']],
    });
    await flush();

    const messages = warn.mock.calls.map((c) => String(c[0]));
    expect(messages.filter((m) => m.includes('Compared flight not among the previous flights'))).toHaveLength(1);
    expect(messages.find((m) => m.includes('not among'))).toContain('ift-12.json');
    warn.mockRestore();
  });

  it('reports the dashboard comparisons with their resolved colours', async () => {
    // setup() keeps no handle; build a second one on the same container
    const container = await setup();
    vi.mocked(fetchPreviousFlightData).mockResolvedValue(EMPTY_FLIGHT);
    const graphs = new TimeSeriesGraphs(container, { subscribe: vi.fn() } as never);
    await flush();
    toggle(container, 'flight-b.json');
    await flush();

    expect(graphs.overlaySnapshot()).toEqual({ v: 1, x: [['flight-b.json', '#00bcd4']] });
  });
});
