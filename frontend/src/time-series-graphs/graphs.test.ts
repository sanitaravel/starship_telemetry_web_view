/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { TimeSeriesStore } from '../state';

vi.mock('chart.js/auto', () => {
  class Chart {
    data: unknown = { datasets: [] };
    update = vi.fn();
    destroy = vi.fn();
    resetZoom = vi.fn();
    static register = vi.fn();
    constructor(public canvas: HTMLCanvasElement) {}
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

import { TimeSeriesGraphs } from './graphs';
import { fetchPreviousFlightList, fetchPreviousFlightData } from '../previous-flights';
import { saveCanvasAsPng } from './export-png';

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

describe('TimeSeriesGraphs Save PNG button', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    vi.mocked(saveCanvasAsPng).mockReset();
  });

  it('exports the chart canvas named after the selected series', async () => {
    const container = await setup();
    const select = container.querySelector('.time-series-graphs__select') as HTMLSelectElement;
    select.value = 'accelerationStarship';
    select.dispatchEvent(new Event('change'));

    (container.querySelector('.time-series-graphs__save-btn') as HTMLButtonElement).click();

    expect(saveCanvasAsPng).toHaveBeenCalledTimes(1);
    const [canvas, filename] = vi.mocked(saveCanvasAsPng).mock.calls[0];
    expect(canvas).toBe(container.querySelector('#chart-main'));
    expect(filename).toMatch(/^starship-accelerationStarship-.*\.png$/);
  });
});
