/**
 * Architecture guard: layering and import cycles across src/.
 *
 * Layers, lowest to highest. A module may import only from its own layer or
 * a lower one. Type-only imports count: they still couple the modules.
 *
 *   core 0 < domain 1 < api 2 < state 3 < ui 4 < features 5 < app 6
 *
 * Every module lives under a layer folder (core/, domain/, ...) and gets that
 * layer; a file anywhere else fails the test. Upward edges and cycles that
 * exist today are listed in ALLOWED_UPWARD_EDGES and ALLOWED_CYCLES; both
 * lists only shrink: an entry that no longer occurs fails the test.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs';
import { join, relative, posix } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('.', import.meta.url));

const LAYERS = ['core', 'domain', 'api', 'state', 'ui', 'features', 'app'] as const;
type Layer = (typeof LAYERS)[number];
const RANK: Record<Layer, number> = Object.fromEntries(LAYERS.map((l, i) => [l, i])) as Record<
  Layer,
  number
>;

/**
 * Upward imports that exist today, as 'from -> to' (src-relative, no `.ts`).
 * Fix one, then delete its line; a stale entry fails the test.
 */
const ALLOWED_UPWARD_EDGES: readonly string[] = [];

/**
 * Import cycles that exist today, as 'a -> b -> ... -> a', rotated to start
 * at the lexicographically smallest member. Delete an entry once broken.
 */
const ALLOWED_CYCLES: readonly string[] = [];

// ---------------------------------------------------------------------------

function listModules(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name === '__snapshots__') continue;
      out.push(...listModules(full));
    } else if (name.endsWith('.ts') && !name.endsWith('.test.ts') && !name.endsWith('.d.ts')) {
      out.push(full);
    }
  }
  return out;
}

function moduleId(file: string): string {
  return relative(SRC, file).split('\\').join('/').replace(/\.ts$/, '');
}

/** Static `import ... from`, `export ... from`, side-effect `import '...'`, and `import('...')`. */
const SPECIFIER_PATTERNS = [
  /^\s*(?:import|export)\b[^;'"`]*?\bfrom\s*['"]([^'"]+)['"]/gm,
  /^\s*import\s*['"]([^'"]+)['"]/gm,
  /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
];

function extractSpecifiers(source: string): string[] {
  const specs: string[] = [];
  for (const pattern of SPECIFIER_PATTERNS) {
    for (const match of source.matchAll(pattern)) specs.push(match[1]);
  }
  return specs;
}

/** Resolve a relative specifier to a module id, or null for CSS / non-relative imports. */
function resolveSpecifier(fromId: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null;
  if (/\.(css|scss|json|svg|png)$/.test(spec)) return null;
  const base = posix.normalize(posix.join(posix.dirname(fromId), spec)).replace(/\.ts$/, '');
  if (existsSync(join(SRC, `${base}.ts`))) return base;
  if (existsSync(join(SRC, base, 'index.ts'))) return `${base}/index`;
  throw new Error(`Unresolvable import '${spec}' in src/${fromId}.ts`);
}

function layerOf(id: string): Layer | null {
  const top = id.split('/')[0];
  if ((LAYERS as readonly string[]).includes(top) && id.includes('/')) return top as Layer;
  return null;
}

interface Graph {
  modules: string[];
  edges: Map<string, Set<string>>;
}

function buildGraph(): Graph {
  const files = listModules(SRC);
  const modules = files.map(moduleId).sort();
  const edges = new Map<string, Set<string>>();
  for (const file of files) {
    const id = moduleId(file);
    const targets = new Set<string>();
    for (const spec of extractSpecifiers(readFileSync(file, 'utf-8'))) {
      const target = resolveSpecifier(id, spec);
      if (target !== null && target !== id) targets.add(target);
    }
    edges.set(id, targets);
  }
  return { modules, edges };
}

/** Rotate a cycle (without the repeated closing node) to start at its smallest member. */
function normaliseCycle(nodes: string[]): string {
  let start = 0;
  for (let i = 1; i < nodes.length; i++) if (nodes[i] < nodes[start]) start = i;
  const rotated = [...nodes.slice(start), ...nodes.slice(0, start)];
  return [...rotated, rotated[0]].join(' -> ');
}

/** Every elementary cycle (Johnson-style DFS from each start node, visiting only larger nodes). */
function findCycles(graph: Graph): string[] {
  const found = new Set<string>();
  const order = graph.modules;
  for (let s = 0; s < order.length; s++) {
    const start = order[s];
    const allowed = new Set(order.slice(s));
    const path: string[] = [start];
    const onPath = new Set<string>([start]);
    const dfs = (node: string): void => {
      for (const next of graph.edges.get(node) ?? []) {
        if (!allowed.has(next)) continue;
        if (next === start) {
          found.add(normaliseCycle([...path]));
        } else if (!onPath.has(next)) {
          path.push(next);
          onPath.add(next);
          dfs(next);
          path.pop();
          onPath.delete(next);
        }
      }
    };
    dfs(start);
  }
  return [...found].sort();
}

function bullet(items: string[]): string {
  return items.map((i) => `  - ${i}`).join('\n');
}

describe('architecture', () => {
  const graph = buildGraph();

  it('finds the source modules and their imports', () => {
    expect(graph.modules).toContain('app/main');
    expect(graph.edges.get('app/main')?.size ?? 0).toBeGreaterThan(5);
  });

  it('extracts every import form, type-only ones included', () => {
    const src = [
      "import { a } from './a';",
      "import type { B } from './b';",
      "import {\n  c,\n  type D,\n} from './c';",
      "export { e } from './e';",
      "export type { F } from './f';",
      "export * from './g';",
      "import './h.css';",
      "const i = await import('./i');",
      "type J = typeof import('./j');",
    ].join('\n');
    expect(extractSpecifiers(src).sort()).toEqual(
      ['./a', './b', './c', './e', './f', './g', './h.css', './i', './j'].sort(),
    );
  });

  it('assigns every module to a layer', () => {
    const unmapped = graph.modules.filter((id) => layerOf(id) === null);
    expect(
      unmapped,
      `Modules outside a layer folder. Move each into one of ` +
        `${LAYERS.map((l) => `${l}/`).join(', ')}:\n${bullet(unmapped)}`,
    ).toEqual([]);
  });

  it('imports only from the same or a lower layer', () => {
    const upward: string[] = [];
    for (const [from, targets] of graph.edges) {
      const fromLayer = layerOf(from);
      if (fromLayer === null) continue; // reported by the layer-assignment test
      for (const to of targets) {
        const toLayer = layerOf(to);
        if (toLayer === null) continue;
        if (RANK[toLayer] > RANK[fromLayer]) upward.push(`${from} -> ${to}`);
      }
    }
    upward.sort();
    const allowed = new Set(ALLOWED_UPWARD_EDGES);
    const violations = upward
      .filter((e) => !allowed.has(e))
      .map((e) => {
        const [from, to] = e.split(' -> ');
        return `${e}   (${layerOf(from)} -> ${layerOf(to)})`;
      });
    const stale = ALLOWED_UPWARD_EDGES.filter((e) => !upward.includes(e));
    expect.soft(
      violations,
      `Upward imports (a module may import only from its own or a lower layer: ` +
        `${LAYERS.join(' < ')}):\n${bullet(violations)}`,
    ).toEqual([]);
    expect(
      stale,
      `ALLOWED_UPWARD_EDGES entries that no longer occur — stale, remove them:\n${bullet(stale)}`,
    ).toEqual([]);
  });

  it('has no import cycles', () => {
    const cycles = findCycles(graph);
    const allowed = new Set(ALLOWED_CYCLES);
    const fresh = cycles.filter((c) => !allowed.has(c));
    const stale = ALLOWED_CYCLES.filter((c) => !cycles.includes(c));
    expect.soft(fresh, `Import cycles:\n${bullet(fresh)}`).toEqual([]);
    expect(
      stale,
      `ALLOWED_CYCLES entries that no longer occur — stale, remove them:\n${bullet(stale)}`,
    ).toEqual([]);
  });
});
