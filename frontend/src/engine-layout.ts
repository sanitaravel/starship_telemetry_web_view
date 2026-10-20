/**
 * Engine layout of both vehicles, shared by the engine visualizer and the
 * engine event detector. Positions and ring groups mirror the subgroups of
 * diagrams/superheavy_engine_diagram.svg and diagrams/starship_engine_diagram.svg
 * (engine-layout.test.ts checks them against the SVGs).
 */

export type Vehicle = 'superheavy' | 'starship';

/** SVG subgroup id an engine belongs to. */
export type EngineRing = 'engines_inner' | 'engines_middle' | 'engines_outer' | 'atmo' | 'vacuum';

export interface EnginePosition {
  id: string;
  cx: number;
  cy: number;
  r: number;
  vehicle: Vehicle;
  ring: EngineRing;
}

/** Human-readable ring names, e.g. for "E17 (outer ring)". */
export const RING_NAMES: Record<EngineRing, string> = {
  engines_inner: 'inner ring',
  engines_middle: 'middle ring',
  engines_outer: 'outer ring',
  atmo: 'sea-level',
  vacuum: 'vacuum',
};

function ss(id: string, cx: number, cy: number, r: number, ring: EngineRing): EnginePosition {
  return { id, cx, cy, r, vehicle: 'starship', ring };
}

function sh(id: string, cx: number, cy: number, ring: EngineRing): EnginePosition {
  return { id, cx, cy, r: 5.5, vehicle: 'superheavy', ring };
}

/** Starship engine layout (native 84×76 coordinate space) */
export const STARSHIP_ENGINES: readonly EnginePosition[] = [
  // Atmospheric (sea-level) engines
  ss('ss_e1', 42, 21, 5.5, 'atmo'),
  ss('ss_e2', 50, 35, 5.5, 'atmo'),
  ss('ss_e3', 34, 35, 5.5, 'atmo'),
  // Vacuum engines
  ss('ss_e4', 69, 15, 14.5, 'vacuum'),
  ss('ss_e5', 42, 61, 14.5, 'vacuum'),
  ss('ss_e6', 15, 15, 14.5, 'vacuum'),
];

/** Super Heavy engine layout (native 96×96 coordinate space, from superheavy_engine_diagram.svg) */
export const SUPERHEAVY_ENGINES: readonly EnginePosition[] = [
  // Inner ring (3 engines)
  sh('sh_e1', 56, 43, 'engines_inner'),
  sh('sh_e2', 48, 57, 'engines_inner'),
  sh('sh_e3', 40, 43, 'engines_inner'),
  // Middle ring (10 engines)
  sh('sh_e4', 56, 24, 'engines_middle'),
  sh('sh_e5', 69, 33, 'engines_middle'),
  sh('sh_e6', 74, 48, 'engines_middle'),
  sh('sh_e7', 69, 63, 'engines_middle'),
  sh('sh_e8', 56, 72, 'engines_middle'),
  sh('sh_e9', 40, 72, 'engines_middle'),
  sh('sh_e10', 27, 63, 'engines_middle'),
  sh('sh_e11', 22, 48, 'engines_middle'),
  sh('sh_e12', 27, 33, 'engines_middle'),
  sh('sh_e13', 40, 24, 'engines_middle'),
  // Outer ring (20 engines)
  sh('sh_e14', 55, 6, 'engines_outer'),
  sh('sh_e15', 67, 10, 'engines_outer'),
  sh('sh_e16', 78, 18, 'engines_outer'),
  sh('sh_e17', 86, 29, 'engines_outer'),
  sh('sh_e18', 90, 41, 'engines_outer'),
  sh('sh_e19', 90, 55, 'engines_outer'),
  sh('sh_e20', 86, 67, 'engines_outer'),
  sh('sh_e21', 78, 78, 'engines_outer'),
  sh('sh_e22', 68, 86, 'engines_outer'),
  sh('sh_e23', 55, 90, 'engines_outer'),
  sh('sh_e24', 41, 90, 'engines_outer'),
  sh('sh_e25', 28, 86, 'engines_outer'),
  sh('sh_e26', 18, 78, 'engines_outer'),
  sh('sh_e27', 10, 67, 'engines_outer'),
  sh('sh_e28', 6, 55, 'engines_outer'),
  sh('sh_e29', 6, 41, 'engines_outer'),
  sh('sh_e30', 10, 29, 'engines_outer'),
  sh('sh_e31', 18, 18, 'engines_outer'),
  sh('sh_e32', 29, 10, 'engines_outer'),
  sh('sh_e33', 41, 6, 'engines_outer'),
];

const BY_ID: ReadonlyMap<string, EnginePosition> = new Map(
  [...STARSHIP_ENGINES, ...SUPERHEAVY_ENGINES].map((e) => [e.id, e]),
);

/** All engines of a vehicle, in id order. */
export function enginesOf(vehicle: Vehicle): readonly EnginePosition[] {
  return vehicle === 'starship' ? STARSHIP_ENGINES : SUPERHEAVY_ENGINES;
}

/** Layout entry of an engine id (case-insensitive), or undefined if unknown. */
export function engineById(id: string): EnginePosition | undefined {
  return BY_ID.get(id.toLowerCase());
}

/** Ring (SVG subgroup id) of an engine, or undefined if the id is unknown. */
export function ringOf(id: string): EngineRing | undefined {
  return engineById(id)?.ring;
}

/** Display name of the ring an engine belongs to, e.g. "outer ring". */
export function ringNameOf(id: string): string | undefined {
  const ring = ringOf(id);
  return ring === undefined ? undefined : RING_NAMES[ring];
}

/** Engines of a vehicle that belong to a ring. */
export function enginesInRing(vehicle: Vehicle, ring: EngineRing): readonly EnginePosition[] {
  return enginesOf(vehicle).filter((e) => e.ring === ring);
}

/** Display label of an engine without its vehicle prefix: "sh_e17" -> "E17". */
export function engineLabel(id: string): string {
  return id.replace(/^(ss|sh)_/i, '').toUpperCase();
}
