import type { TimeSeriesStore } from '../../core/time-series';
import type { EngineEvent } from '../../domain/engines/events';
import { computeAcceleration, IncrementalXY } from '../../domain/series-math';
import type { DatasetKey, LoadedComparison, SeriesKey, SeriesOption } from './types';
import { eventMarkers, eventsForSeries, seriesVehicle, type EngineMarker } from './plugins/engine-markers';

export type XY = { x: number; y: number | null };

/**
 * Chart data for the live flight and the comparison flights, built from the
 * stores in seconds from T-0 (sub-second where available), sorted and
 * deduplicated so the line never loops. Derived series (acceleration) come
 * from their base speed series.
 */
export class ChartData {
  /** The live series' chart data, extended in place as points arrive. */
  private liveXY = new IncrementalXY();
  /** Chart data per comparison flight and series; flights never change. */
  private overlayXY: Map<string, Map<SeriesKey, XY[]>> = new Map();

  /** The live series is extended incrementally (see IncrementalXY). */
  live(option: SeriesOption, store: TimeSeriesStore): XY[] {
    if (option.derive) {
      return computeAcceleration(store[option.derive.from] ?? []);
    }
    return this.liveXY.update(store[option.key as DatasetKey] ?? []);
  }

  /** Chart data for a comparison flight, computed once per flight and series. */
  comparison(option: SeriesOption, comp: LoadedComparison): XY[] {
    let perSeries = this.overlayXY.get(comp.filename);
    if (!perSeries) {
      perSeries = new Map();
      this.overlayXY.set(comp.filename, perSeries);
    }
    let xy = perSeries.get(option.key);
    if (!xy) {
      xy = option.derive
        ? computeAcceleration(comp.data[option.derive.from] ?? [])
        : new IncrementalXY().update(comp.data[option.key as DatasetKey] ?? []);
      perSeries.set(option.key, xy);
    }
    return xy;
  }
}

/** Datasets for the live series (if it has data) followed by one overlay per comparison flight. */
export function buildDatasets(
  option: SeriesOption,
  liveXY: XY[],
  comparisons: LoadedComparison[],
  comparisonData: (comp: LoadedComparison) => XY[],
  colorOf: (filename: string) => string,
): any[] {
  const datasets: any[] = [];

  // Only include the live dataset if there's actual data
  if (liveXY.length > 0) {
    datasets.push({
      label: option.label,
      data: liveXY,
      borderColor: option.lineColor,
      backgroundColor: `${option.lineColor}22`,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.2,
      // Acceleration swings above and below zero, so skip the area fill.
      fill: !option.derive,
      spanGaps: false,
      // Data is already {x, y}, sorted by x and unique, so Chart.js can
      // skip parsing and its own sorting/uniqueness checks.
      parsing: false,
      normalized: true,
    });
  }

  // Add each comparison flight as an overlay dataset
  for (let i = 0; i < comparisons.length; i++) {
    const comp = comparisons[i];
    const compXY = comparisonData(comp);
    const color = colorOf(comp.filename);

    datasets.push({
      label: `${option.label} (${comp.name})`,
      data: compXY,
      borderColor: color,
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      borderDash: [6, 3],
      pointRadius: 0,
      pointHoverRadius: 3,
      tension: 0.2,
      fill: false,
      spanGaps: false,
      parsing: false,
      normalized: true,
    });
  }
  return datasets;
}

/**
 * Engine event markers for a series: the live flight's in red/amber, each
 * comparison flight's in its overlay colour (see eventsForSeries).
 */
export function buildMarkers(
  option: SeriesOption,
  liveEvents: EngineEvent[],
  liveSeparationMet: number | null,
  comparisons: LoadedComparison[],
  colorOf: (filename: string) => string,
): EngineMarker[] {
  const vehicle = seriesVehicle(option.derive?.from ?? option.key);
  const markers = eventMarkers(eventsForSeries(liveEvents ?? [], vehicle, liveSeparationMet ?? null));
  for (const comp of comparisons) {
    markers.push(
      ...eventMarkers(
        eventsForSeries(comp.engineEvents, vehicle, comp.separationMetSeconds),
        colorOf(comp.filename),
        comp.name,
      ),
    );
  }
  return markers;
}
