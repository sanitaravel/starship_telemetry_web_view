import { geoGraticule10 } from 'd3-geo';
import { feature, merge, mesh } from 'topojson-client';
import type { GeometryCollection, MultiPolygon, Polygon, Topology } from 'topojson-specification';
import countriesTopology from 'world-atlas/countries-110m.json';
import { rotationToCenter, type Rotation } from './geo';
import { countryLabels } from './labels';

export const COLORS = {
  ocean: '#1c1c1c',
  land: '#3d3d3d',
  border: 'rgba(254, 254, 254, 0.22)',
  countryLabel: 'rgba(254, 254, 254, 0.55)',
  countryLabelHalo: 'rgba(38, 38, 38, 0.7)',
  graticule: 'rgba(254, 254, 254, 0.07)',
  rim: '#444444',
  ship: '#FF8014',
  shipHalo: 'rgba(255, 128, 20, 0.25)',
  label: '#FEFEFE',
};

type CountryCollection = GeometryCollection & { geometries: (Polygon | MultiPolygon)[] };
const countries = countriesTopology as unknown as Topology<{ countries: CountryCollection }>;
/** All countries merged into one landmass for the fill. */
export const LAND = merge(countries, countries.objects.countries.geometries);
/** Borders shared by two countries only, so coastlines are not outlined. */
export const BORDERS = mesh(countries, countries.objects.countries, (a, b) => a !== b);
/** Country name labels, largest country first; which ones show depends on zoom. */
export const COUNTRY_LABELS = countryLabels(
  feature(countries, countries.objects.countries).features as Parameters<typeof countryLabels>[0],
);
export const LABEL_FONT = '400 10px "JetBrains Mono", monospace';
export const LABEL_HEIGHT = 10;
export const GRATICULE = geoGraticule10();

/** Path gradient: number of slices, and opacity / line width at the start and the end. */
export const GRADIENT_STEPS = 48;
export const GRADIENT_ALPHA: [number, number] = [0.15, 1];
export const GRADIENT_WIDTH: [number, number] = [1.25, 3];

/** Map area height, and so the globe's diameter at 1× zoom, in CSS pixels. */
export const MAX_GLOBE_SIZE = 640;

/** Zoom multiplier per +/− click, and per pixel of wheel delta (exponential). */
export const ZOOM_STEP = 1.5;
export const WHEEL_ZOOM_SENSITIVITY = 0.0015;

/** Default view before any ship is known: Starbase, Texas. */
export const DEFAULT_ROTATION: Rotation = rotationToCenter(-97.157, 25.997);

/** Globe radius in CSS pixels at 1× zoom. */
export function baseRadius(width: number, height: number): number {
  return Math.min(width, height) / 2 - 2;
}
