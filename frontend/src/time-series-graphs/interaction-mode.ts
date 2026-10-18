import type { Chart, ChartEvent, InteractionItem, InteractionModeFunction } from 'chart.js/auto';
import { getRelativePosition } from 'chart.js/helpers';

/** Name of the interaction mode, for `interaction.mode` in the chart options. */
export const NEAREST_PER_DATASET = 'nearestPerDataset';

/**
 * Largest horizontal distance, in pixels, between the cursor and a dataset's
 * nearest point for that dataset to be listed. Beyond it the dataset has no
 * data at that time (a gap, or a flight that ended earlier).
 */
export const MAX_POINT_DISTANCE_PX = 40;

declare module 'chart.js' {
  interface InteractionModeMap {
    nearestPerDataset: InteractionModeFunction;
  }
}

/**
 * Interaction mode: the point nearest the cursor's x in every visible dataset.
 *
 * Chart.js's 'nearest' mode returns only the single closest point overall, and
 * 'index' assumes all datasets share x values. The live flight and each
 * comparison flight have their own time points, so neither lists every
 * flight at the hovered time; this mode does. Data must be sorted by x (the
 * chart's series are).
 */
export const nearestPerDataset: InteractionModeFunction = (
  chart: Chart,
  e: ChartEvent,
  _options,
  useFinalPosition,
): InteractionItem[] => {
  const { x } = getRelativePosition(e, chart);
  const items: InteractionItem[] = [];

  chart.data.datasets.forEach((_dataset, datasetIndex) => {
    if (!chart.isDatasetVisible(datasetIndex)) return;
    const elements = chart.getDatasetMeta(datasetIndex).data;
    const pixelX = (index: number) =>
      elements[index].getProps(['x'], useFinalPosition).x as number;
    // Line points carry `skip` for gap markers (null values)
    const isGap = (index: number) => (elements[index] as { skip?: boolean }).skip === true;
    if (elements.length === 0) return;

    // Binary search for the first point at or right of the cursor
    let lo = 0;
    let hi = elements.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (pixelX(mid) < x) lo = mid + 1;
      else hi = mid;
    }

    // Walk outwards to the nearest point that is drawn (gap markers are skipped)
    let best = -1;
    let bestDistance = MAX_POINT_DISTANCE_PX;
    for (let i = lo - 1; i >= 0; i--) {
      const distance = x - pixelX(i);
      if (distance > bestDistance) break;
      if (!isGap(i)) {
        best = i;
        bestDistance = distance;
        break;
      }
    }
    for (let i = lo; i < elements.length; i++) {
      const distance = pixelX(i) - x;
      if (distance > bestDistance) break;
      if (!isGap(i)) {
        if (distance < bestDistance || best === -1) best = i;
        break;
      }
    }

    if (best !== -1) {
      items.push({ element: elements[best], datasetIndex, index: best });
    }
  });

  return items;
};
