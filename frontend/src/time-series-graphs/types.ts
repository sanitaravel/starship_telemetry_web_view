import type { TimeSeriesStore } from '../state';

export type DatasetKey = 'speedSuperHeavy' | 'speedStarship' | 'altitudeSuperHeavy' | 'altitudeStarship';

export interface SeriesOption {
  key: DatasetKey;
  label: string;
  yLabel: string;
  lineColor: string;
}

export interface LoadedComparison {
  filename: string;
  name: string;
  data: TimeSeriesStore;
}
