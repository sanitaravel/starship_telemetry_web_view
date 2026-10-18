import { describe, it, expect } from 'vitest';
import { nearestPerDataset, MAX_POINT_DISTANCE_PX } from './interaction-mode';

/** A dataset's drawn points: pixel x per point, and which are gap markers. */
function points(xs: number[], skipped: number[] = []) {
  return xs.map((x, i) => ({ x, skip: skipped.includes(i), getProps: () => ({ x }) }));
}

function fakeChart(datasets: ReturnType<typeof points>[], hidden: number[] = []) {
  return {
    data: { datasets: datasets.map(() => ({})) },
    isDatasetVisible: (i: number) => !hidden.includes(i),
    getDatasetMeta: (i: number) => ({ data: datasets[i] }),
  };
}

function hover(chart: ReturnType<typeof fakeChart>, x: number) {
  const event = { native: {}, x, y: 0 } as never;
  return nearestPerDataset(chart as never, event, {} as never, false).map((item) => [
    item.datasetIndex,
    item.index,
  ]);
}

describe('nearestPerDataset', () => {
  it('lists the nearest point of every flight, each on its own time points', () => {
    const live = points([0, 10, 20, 30, 40]);
    const ift13 = points([3, 13, 23, 33]);
    const ift14 = points([6, 16, 26, 36]);
    expect(hover(fakeChart([live, ift13, ift14]), 24)).toEqual([
      [0, 2],
      [1, 2],
      [2, 2],
    ]);
  });

  it('skips gap markers and leaves out a flight with no data near the cursor', () => {
    const live = points([0, 10, 20, 30], [2]); // gap marker at x=20
    const ended = points([0, 10]); // a flight that ended earlier
    // Nearest drawn live point to 21 is 30 (20 is a gap marker, 10 is further)
    expect(hover(fakeChart([live]), 21)).toEqual([[0, 3]]);
    // At 60 the live point at 30 is in reach, the ended flight's 10 is not
    expect(hover(fakeChart([live, ended]), 30 + MAX_POINT_DISTANCE_PX - 10)).toEqual([[0, 3]]);
    expect(hover(fakeChart([live, ended]), 30 + MAX_POINT_DISTANCE_PX + 10)).toEqual([]);
  });

  it('ignores hidden datasets', () => {
    expect(hover(fakeChart([points([0, 10]), points([1, 11])], [1]), 9)).toEqual([[0, 1]]);
  });
});
