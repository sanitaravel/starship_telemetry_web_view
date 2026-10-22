import type { EngineStatus } from '../../domain/engines/events';
import {
  STARSHIP_ENGINES,
  SUPERHEAVY_ENGINES,
  engineLabel,
  type EnginePosition,
} from '../../domain/engines/layout';
import { svgEl } from '../../ui/svg';

/** Display sizes for the rendered SVGs (used as fallback, CSS overrides with 100% width) */
const SUPERHEAVY_DISPLAY_SIZE = 420;
const STARSHIP_DISPLAY_WIDTH = 420;
const STARSHIP_DISPLAY_HEIGHT = Math.round(420 * (76 / 84));

/**
 * ViewBox padding to prevent clipping of outermost circles.
 * Super Heavy outermost engines (e.g., E18 at cx=90, E29 at cx=6) with r=5.5
 * extend to x=95.5 and x=0.5, so minimal padding is sufficient.
 */
const SUPERHEAVY_PADDING = 2;
const STARSHIP_PADDING = 2;

/** Native coordinate spaces */
const SUPERHEAVY_NATIVE = 96;
const STARSHIP_NATIVE_W = 84;
const STARSHIP_NATIVE_H = 76;

/** One vehicle's engine diagram and its engine circles by lower-case engine id. */
export interface EngineDiagram {
  svg: SVGSVGElement;
  circles: Map<string, SVGGElement>;
}

/**
 * Font size that fits `label` inside a circle of radius `r`.
 * For monospace, approximate char width ≈ 0.6 * fontSize; text must fit
 * within ~1.6 * r of usable horizontal space.
 */
export function fitFontSize(label: string, r: number): number {
  const maxWidthBasedSize = (r * 1.6) / (label.length * 0.6);
  const maxHeightBasedSize = r * 1.0;
  return Math.min(maxWidthBasedSize, maxHeightBasedSize);
}

/**
 * Returns the fill color for an engine status.
 */
export function getStatusFill(status: EngineStatus): string {
  switch (status) {
    case 'active':
      return '#FF8014';
    case 'inactive':
      return '#1a1a1a';
    case 'undetected':
      return '#666666';
  }
}

/**
 * Returns the stroke color for an engine status.
 */
export function getStatusStroke(status: EngineStatus): string {
  switch (status) {
    case 'active':
      return '#FFa040';
    case 'inactive':
      return '#444444';
    case 'undetected':
      return '#888888';
  }
}

/**
 * Create the Super Heavy engine SVG diagram.
 */
export function createSuperHeavyDiagram(): EngineDiagram {
  const vbSize = SUPERHEAVY_NATIVE + SUPERHEAVY_PADDING * 2;
  const svg = svgEl('svg', {
    viewBox: `${-SUPERHEAVY_PADDING} ${-SUPERHEAVY_PADDING} ${vbSize} ${vbSize}`,
    width: String(SUPERHEAVY_DISPLAY_SIZE),
    height: String(SUPERHEAVY_DISPLAY_SIZE),
    'aria-label': 'Super Heavy engine status diagram',
  });
  svg.classList.add('engine-visualizer__svg');
  return { svg, circles: addEngineCircles(svg, SUPERHEAVY_ENGINES) };
}

/**
 * Create the Starship engine SVG diagram.
 */
export function createStarshipDiagram(): EngineDiagram {
  const vbW = STARSHIP_NATIVE_W + STARSHIP_PADDING * 2;
  const vbH = STARSHIP_NATIVE_H + STARSHIP_PADDING * 2;
  const svg = svgEl('svg', {
    viewBox: `${-STARSHIP_PADDING} ${-STARSHIP_PADDING} ${vbW} ${vbH}`,
    width: String(STARSHIP_DISPLAY_WIDTH),
    height: String(STARSHIP_DISPLAY_HEIGHT),
    'aria-label': 'Starship engine status diagram',
  });
  svg.classList.add('engine-visualizer__svg');
  return { svg, circles: addEngineCircles(svg, STARSHIP_ENGINES) };
}

/** Appends one undetected circle per engine to `svg`, in layout order. */
function addEngineCircles(svg: SVGSVGElement, engines: readonly EnginePosition[]): Map<string, SVGGElement> {
  const circles = new Map<string, SVGGElement>();
  for (const engine of engines) {
    const circle = createEngineCircle(engine, 'undetected');
    svg.appendChild(circle);
    circles.set(engine.id.toLowerCase(), circle);
  }
  return circles;
}

/**
 * Create a single engine circle with text label inside.
 * Font size is adjusted based on both circle radius and label length
 * to ensure text fits within the circle.
 */
function createEngineCircle(engine: EnginePosition, status: EngineStatus): SVGGElement {
  const group = svgEl('g');

  const circle = svgEl('circle', {
    cx: String(engine.cx),
    cy: String(engine.cy),
    r: String(engine.r),
    fill: getStatusFill(status),
    stroke: getStatusStroke(status),
    'stroke-width': '0.5',
  });

  const label = engineLabel(engine.id);
  const text = svgEl('text', {
    x: String(engine.cx),
    y: String(engine.cy),
    'text-anchor': 'middle',
    'dominant-baseline': 'central',
    fill: '#FEFEFE',
    'font-family': "'JetBrains Mono', monospace",
    'font-weight': '500',
    'font-size': fitFontSize(label, engine.r).toFixed(2),
  });
  text.textContent = label;

  group.dataset.label = label;
  group.setAttribute('role', 'img');
  group.setAttribute('aria-label', label);
  group.appendChild(circle);
  group.appendChild(text);

  return group;
}
