import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { Feature, Geometry } from 'geojson';
import { apparentSize, countryLabels, overlaps, placeLabels, type CountryLabel } from './labels';

/** Axis-aligned lon/lat square as a GeoJSON ring (counter-clockwise for d3). */
function square(lon: number, lat: number, size: number): [number, number][] {
  return [
    [lon, lat],
    [lon, lat + size],
    [lon + size, lat + size],
    [lon + size, lat],
    [lon, lat],
  ];
}

function country(name: string, geometry: Geometry): Feature<Geometry, { name: string }> {
  return { type: 'Feature', properties: { name }, geometry };
}

/** Flat test projection: 10 px per degree around (500, 500). */
const project = ([lon, lat]: [number, number]): [number, number] => [500 + lon * 10, 500 - lat * 10];
const viewport = { width: 1000, height: 1000 };
const measure = (name: string) => name.length * 6;

describe('countryLabels', () => {
  it('orders labels largest first and anchors multi-part countries on the largest part', () => {
    const labels = countryLabels([
      country('Small', { type: 'Polygon', coordinates: [square(0, 0, 1)] }),
      country('Split', {
        type: 'MultiPolygon',
        coordinates: [[square(40, 0, 1)], [square(10, 0, 10)]],
      }),
    ]);

    expect(labels.map((l) => l.name)).toEqual(['Split', 'Small']);
    const [lon, lat] = labels[0].anchor;
    expect(lon).toBeCloseTo(15, 0);
    expect(lat).toBeCloseTo(5, 0);
  });

  it('skips unnamed and non-polygon features', () => {
    expect(
      countryLabels([
        { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [square(0, 0, 1)] } },
        country('Line', { type: 'LineString', coordinates: [[0, 0], [1, 1]] }),
      ]),
    ).toEqual([]);
  });
});

describe('apparentSize', () => {
  it('grows with zoom and shrinks toward the edge of the globe', () => {
    expect(apparentSize(0.01, 600, 0)).toBeGreaterThan(apparentSize(0.01, 300, 0));
    expect(apparentSize(0.01, 300, 1)).toBeLessThan(apparentSize(0.01, 300, 0));
  });
});

describe('placeLabels', () => {
  const big: CountryLabel = { name: 'Bigland', anchor: [0, 0], area: 0.4 };
  const tiny: CountryLabel = { name: 'Tinyland', anchor: [20, 0], area: 0.0005 };

  it('shows small countries only once zoomed in far enough', () => {
    const at1x = placeLabels([big, tiny], [0, 0], 300, project, measure, 10, viewport);
    const at8x = placeLabels([big, tiny], [0, 0], 2400, project, measure, 10, viewport);
    expect(at1x.map((l) => l.name)).toEqual(['Bigland']);
    expect(at8x.map((l) => l.name)).toEqual(['Bigland', 'Tinyland']);
  });

  it('skips a label that would overlap a larger country label', () => {
    const neighbour: CountryLabel = { name: 'Nextdoor', anchor: [0.5, 0], area: 0.3 };
    const placed = placeLabels([big, neighbour], [0, 0], 2400, project, measure, 10, viewport);
    expect(placed.map((l) => l.name)).toEqual(['Bigland']);
  });

  it('keeps labels out of reserved areas', () => {
    const controls = { x: 450, y: 450, w: 100, h: 100 };
    expect(placeLabels([big], [0, 0], 300, project, measure, 10, viewport, [controls])).toEqual([]);
  });

  it('skips countries on the far side or outside the viewport', () => {
    const farSide: CountryLabel = { name: 'Farside', anchor: [180, 0], area: 0.4 };
    const offscreen: CountryLabel = { name: 'Offscreen', anchor: [60, 0], area: 0.4 };
    const placed = placeLabels([farSide, offscreen], [0, 0], 2400, project, measure, 10, viewport);
    expect(placed).toEqual([]);
  });

  it('never places overlapping labels (property)', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            name: fc.string({ minLength: 1, maxLength: 12 }),
            anchor: fc.tuple(fc.double({ min: -40, max: 40, noNaN: true }), fc.double({ min: -40, max: 40, noNaN: true })),
            area: fc.double({ min: 0, max: 1, noNaN: true }),
          }),
          { maxLength: 40 },
        ),
        fc.double({ min: 100, max: 3000, noNaN: true }),
        (labels, scale) => {
          const placed = placeLabels(labels, [0, 0], scale, project, measure, 10, viewport);
          const boxes = placed.map(({ name, x, y }) => ({ x: x - measure(name) / 2, y: y - 5, w: measure(name), h: 10 }));
          for (let i = 0; i < boxes.length; i++) {
            for (let j = i + 1; j < boxes.length; j++) expect(overlaps(boxes[i], boxes[j])).toBe(false);
          }
        },
      ),
    );
  });
});
