import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  STARSHIP_ENGINES,
  SUPERHEAVY_ENGINES,
  enginesOf,
  enginesInRing,
  engineLabel,
  ringNameOf,
  ringOf,
  type EnginePosition,
} from './engine-layout';

const DIAGRAMS = fileURLToPath(new URL('../../diagrams/', import.meta.url));

interface SvgEngine {
  id: string;
  ring: string;
  cx: number;
  cy: number;
  r: number;
}

/** Every engine circle in a diagram with the id of the (leaf) group around it. */
function svgEngines(file: string): SvgEngine[] {
  const svg = readFileSync(DIAGRAMS + file, 'utf-8');
  const engines: SvgEngine[] = [];
  // Ring groups only contain circles, so the first </g> closes the group.
  for (const group of svg.matchAll(/<g id="([^"]+)">((?:(?!<g[\s>]|<\/g>)[\s\S])*)<\/g>/g)) {
    for (const circle of group[2].matchAll(/<circle\b([^>]*)\/?>/g)) {
      const attr = (name: string) => new RegExp(`\\b${name}="([^"]*)"`).exec(circle[1])?.[1];
      engines.push({
        id: attr('id')!,
        ring: group[1],
        cx: Number(attr('cx')),
        cy: Number(attr('cy')),
        r: Number(attr('r')),
      });
    }
  }
  return engines.sort((a, b) => a.id.localeCompare(b.id));
}

function layoutEngines(engines: readonly EnginePosition[]): SvgEngine[] {
  return engines
    .map(({ id, ring, cx, cy, r }) => ({ id, ring, cx, cy, r }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

describe('engine layout matches the diagrams', () => {
  it('Super Heavy engines, positions and rings', () => {
    const fromSvg = svgEngines('superheavy_engine_diagram.svg');
    expect(fromSvg).toHaveLength(33);
    expect(layoutEngines(SUPERHEAVY_ENGINES)).toEqual(fromSvg);
  });

  it('Starship engines, positions and rings', () => {
    const fromSvg = svgEngines('starship_engine_diagram.svg');
    expect(fromSvg).toHaveLength(6);
    expect(layoutEngines(STARSHIP_ENGINES)).toEqual(fromSvg);
  });
});

describe('engine layout helpers', () => {
  it('looks up rings and names', () => {
    expect(ringOf('sh_e2')).toBe('engines_inner');
    expect(ringOf('SH_E13')).toBe('engines_middle');
    expect(ringNameOf('sh_e17')).toBe('outer ring');
    expect(ringNameOf('ss_e3')).toBe('sea-level');
    expect(ringNameOf('ss_e4')).toBe('vacuum');
    expect(ringOf('xx_e1')).toBeUndefined();
  });

  it('lists engines per vehicle and ring', () => {
    expect(enginesOf('superheavy')).toHaveLength(33);
    expect(enginesOf('starship')).toHaveLength(6);
    expect(enginesInRing('superheavy', 'engines_middle')).toHaveLength(10);
    expect(enginesInRing('superheavy', 'engines_outer')).toHaveLength(20);
    expect(enginesInRing('starship', 'vacuum').map((e) => e.id)).toEqual(['ss_e4', 'ss_e5', 'ss_e6']);
    expect(enginesOf('starship').every((e) => e.vehicle === 'starship')).toBe(true);
  });

  it('labels engines without the vehicle prefix', () => {
    expect(engineLabel('sh_e17')).toBe('E17');
    expect(engineLabel('ss_e1')).toBe('E1');
  });
});
