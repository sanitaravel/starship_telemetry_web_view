/**
 * @vitest-environment jsdom
 */
import { describe, it, expect } from 'vitest';
import {
  createStarshipDiagram,
  createSuperHeavyDiagram,
  fitFontSize,
  getStatusFill,
  getStatusStroke,
} from './diagram';
import { createLegend } from './legend';

describe('engine diagrams', () => {
  it('draws Starship’s 6 engines in a padded 84×76 view box', () => {
    const { svg, circles } = createStarshipDiagram();
    expect(svg.getAttribute('viewBox')).toBe('-2 -2 88 80');
    expect([svg.getAttribute('width'), svg.getAttribute('height')]).toEqual(['420', '380']);
    expect([...circles.keys()]).toEqual(['ss_e1', 'ss_e2', 'ss_e3', 'ss_e4', 'ss_e5', 'ss_e6']);
    expect(svg.querySelectorAll('g')).toHaveLength(6);
  });

  it('draws Super Heavy’s 33 engines in a padded 96×96 view box', () => {
    const { svg, circles } = createSuperHeavyDiagram();
    expect(svg.getAttribute('viewBox')).toBe('-2 -2 100 100');
    expect([svg.getAttribute('width'), svg.getAttribute('height')]).toEqual(['420', '420']);
    expect(circles.size).toBe(33);
    expect(circles.get('sh_e1')!.dataset.label).toBe('E1');
  });

  it('starts every engine undetected, labelled with its E-number', () => {
    const group = createStarshipDiagram().circles.get('ss_e6')!;
    const circle = group.querySelector('circle')!;
    expect([circle.getAttribute('fill'), circle.getAttribute('stroke')]).toEqual(['#666666', '#888888']);
    expect(group.querySelector('text')!.textContent).toBe('E6');
    expect(group.getAttribute('aria-label')).toBe('E6');
  });

  it('fits the label inside the circle', () => {
    expect(fitFontSize('E1', 5)).toBe(5); // height-bound
    expect(fitFontSize('E33', 6)).toBeCloseTo((6 * 1.6) / (3 * 0.6)); // width-bound
  });
});

describe('engine status colours', () => {
  it('fills and strokes each status', () => {
    const statuses = ['active', 'inactive', 'undetected'] as const;
    expect(statuses.map(getStatusFill)).toEqual(['#FF8014', '#1a1a1a', '#666666']);
    expect(statuses.map(getStatusStroke)).toEqual(['#FFa040', '#444444', '#888888']);
  });

  it('the legend shows one swatch per status', () => {
    const items = [...createLegend().querySelectorAll('.engine-visualizer__legend-item')];
    expect(items.map((i) => i.textContent)).toEqual(['Active', 'Inactive', 'Undetected']);
  });
});
