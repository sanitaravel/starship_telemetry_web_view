import type { TimeSeriesStore } from '../state';

export type DatasetKey = 'speedSuperHeavy' | 'speedStarship' | 'altitudeSuperHeavy' | 'altitudeStarship';

/** Keys for series derived from the base store rather than stored directly. */
export type DerivedKey = 'accelerationSuperHeavy' | 'accelerationStarship';

export type SeriesKey = DatasetKey | DerivedKey;

export interface SeriesOption {
  key: SeriesKey;
  label: string;
  yLabel: string;
  lineColor: string;
  /** Set for derived series: the base series and how to transform it. */
  derive?: { from: DatasetKey; kind: 'acceleration' };
}

export interface LoadedComparison {
  filename: string;
  name: string;
  data: TimeSeriesStore;
}
