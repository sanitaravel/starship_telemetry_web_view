import type { StateManager, AppState } from './state';
import { STARSHIP_ENGINES, SUPERHEAVY_ENGINES, engineLabel, type EnginePosition } from './engine-layout';

type EngineStatus = 'active' | 'inactive' | 'undetected';

const SVG_NS = 'http://www.w3.org/2000/svg';

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

/**
 * Returns the fill color for an engine status.
 */
function getStatusFill(status: EngineStatus): string {
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
function getStatusStroke(status: EngineStatus): string {
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
 * Engine Visualizer UI component.
 * Renders SVG diagrams showing the spatial layout of engines for both
 * Super Heavy and Starship vehicles, color-coded by engine status.
 */
export class EngineVisualizer {
  private container: HTMLElement;
  private stateManager: StateManager;
  private unsubscribe: (() => void) | null = null;
  private lastRecord: AppState['latestTelemetry'] = null;
  /** Status each engine circle currently shows, to skip unchanged updates. */
  private renderedStatus: Map<SVGGElement, EngineStatus> = new Map();

  private superheavySvg!: SVGSVGElement;
  private starshipSvg!: SVGSVGElement;
  private superheavyCircles: Map<string, SVGGElement> = new Map();
  private starshipCircles: Map<string, SVGGElement> = new Map();

  constructor(container: HTMLElement, stateManager: StateManager) {
    this.container = container;
    this.stateManager = stateManager;

    this.render();
    this.subscribeToState();
    this.update(this.stateManager.getState());
  }

  /**
   * Build the DOM structure for the engine visualizer.
   */
  private render(): void {
    this.container.innerHTML = '';
    this.container.classList.add('engine-visualizer');

    // Super Heavy section
    const superheavySection = document.createElement('div');
    superheavySection.className = 'engine-visualizer__section';

    const superheavyTitle = document.createElement('h3');
    superheavyTitle.className = 'engine-visualizer__title';
    superheavyTitle.textContent = 'SUPER HEAVY';
    superheavySection.appendChild(superheavyTitle);

    this.superheavySvg = this.createSuperHeavySvg();
    superheavySection.appendChild(this.superheavySvg);

    // Starship section
    const starshipSection = document.createElement('div');
    starshipSection.className = 'engine-visualizer__section';

    const starshipTitle = document.createElement('h3');
    starshipTitle.className = 'engine-visualizer__title';
    starshipTitle.textContent = 'STARSHIP';
    starshipSection.appendChild(starshipTitle);

    this.starshipSvg = this.createStarshipSvg();
    starshipSection.appendChild(this.starshipSvg);

    this.container.appendChild(superheavySection);
    this.container.appendChild(starshipSection);

    // Legend
    const legend = this.createLegend();
    this.container.appendChild(legend);
  }

  /**
   * Create the Super Heavy engine SVG diagram.
   */
  private createSuperHeavySvg(): SVGSVGElement {
    const svg = document.createElementNS(SVG_NS, 'svg');
    const vbSize = SUPERHEAVY_NATIVE + SUPERHEAVY_PADDING * 2;
    svg.setAttribute('viewBox', `${-SUPERHEAVY_PADDING} ${-SUPERHEAVY_PADDING} ${vbSize} ${vbSize}`);
    svg.setAttribute('width', String(SUPERHEAVY_DISPLAY_SIZE));
    svg.setAttribute('height', String(SUPERHEAVY_DISPLAY_SIZE));
    svg.setAttribute('aria-label', 'Super Heavy engine status diagram');
    svg.classList.add('engine-visualizer__svg');

    for (const engine of SUPERHEAVY_ENGINES) {
      const { circle } = this.createEngineCircle(engine, 'undetected');
      svg.appendChild(circle);
      this.superheavyCircles.set(engine.id.toLowerCase(), circle);
    }

    return svg;
  }

  /**
   * Create the Starship engine SVG diagram.
   */
  private createStarshipSvg(): SVGSVGElement {
    const svg = document.createElementNS(SVG_NS, 'svg');
    const vbW = STARSHIP_NATIVE_W + STARSHIP_PADDING * 2;
    const vbH = STARSHIP_NATIVE_H + STARSHIP_PADDING * 2;
    svg.setAttribute('viewBox', `${-STARSHIP_PADDING} ${-STARSHIP_PADDING} ${vbW} ${vbH}`);
    svg.setAttribute('width', String(STARSHIP_DISPLAY_WIDTH));
    svg.setAttribute('height', String(STARSHIP_DISPLAY_HEIGHT));
    svg.setAttribute('aria-label', 'Starship engine status diagram');
    svg.classList.add('engine-visualizer__svg');

    for (const engine of STARSHIP_ENGINES) {
      const { circle } = this.createEngineCircle(engine, 'undetected');
      svg.appendChild(circle);
      this.starshipCircles.set(engine.id.toLowerCase(), circle);
    }

    return svg;
  }

  /**
   * Create the legend showing what each engine status color means.
   */
  private createLegend(): HTMLElement {
    const legend = document.createElement('div');
    legend.className = 'engine-visualizer__legend';
    legend.setAttribute('aria-label', 'Engine status legend');

    const items: { status: EngineStatus; label: string; description: string }[] = [
      { status: 'active', label: 'Active', description: 'Engine is firing' },
      { status: 'inactive', label: 'Inactive', description: 'Engine is off' },
      { status: 'undetected', label: 'Undetected', description: 'Status unknown' },
    ];

    for (const item of items) {
      const entry = document.createElement('div');
      entry.className = 'engine-visualizer__legend-item';

      const swatch = document.createElement('span');
      swatch.className = 'engine-visualizer__legend-swatch';
      swatch.style.backgroundColor = getStatusFill(item.status);
      swatch.style.borderColor = getStatusStroke(item.status);

      const label = document.createElement('span');
      label.className = 'engine-visualizer__legend-label';
      label.textContent = item.label;
      label.title = item.description;

      entry.appendChild(swatch);
      entry.appendChild(label);
      legend.appendChild(entry);
    }

    return legend;
  }

  /**
   * Create a single engine circle with text label inside.
   * Font size is adjusted based on both circle radius and label length
   * to ensure text fits within the circle.
   */
  private createEngineCircle(
    engine: EnginePosition,
    status: EngineStatus
  ): { circle: SVGGElement } {
    const group = document.createElementNS(SVG_NS, 'g');

    const circle = document.createElementNS(SVG_NS, 'circle');
    circle.setAttribute('cx', String(engine.cx));
    circle.setAttribute('cy', String(engine.cy));
    circle.setAttribute('r', String(engine.r));
    circle.setAttribute('fill', getStatusFill(status));
    circle.setAttribute('stroke', getStatusStroke(status));
    circle.setAttribute('stroke-width', '0.5');

    // Display label: strip vehicle prefix (ss_/sh_) for cleaner display
    const displayLabel = engineLabel(engine.id);

    // Scale font-size to fit inside the circle.
    // For monospace, approximate char width ≈ 0.6 * fontSize.
    // Text must fit within ~1.6 * r (usable horizontal space).
    const charCount = displayLabel.length;
    const maxWidthBasedSize = (engine.r * 1.6) / (charCount * 0.6);
    const maxHeightBasedSize = engine.r * 1.0;
    const fontSize = Math.min(maxWidthBasedSize, maxHeightBasedSize);

    const text = document.createElementNS(SVG_NS, 'text');
    text.setAttribute('x', String(engine.cx));
    text.setAttribute('y', String(engine.cy));
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('dominant-baseline', 'central');
    text.setAttribute('fill', '#FEFEFE');
    text.setAttribute('font-size', String(fontSize.toFixed(2)));
    text.setAttribute('font-family', "'JetBrains Mono', monospace");
    text.setAttribute('font-weight', '500');
    text.textContent = displayLabel;

    group.appendChild(circle);
    group.appendChild(text);

    return { circle: group };
  }

  /**
   * Subscribe to state changes.
   */
  private subscribeToState(): void {
    this.unsubscribe = this.stateManager.subscribe((state) => this.update(state));
  }

  /**
   * Update engine visualizations based on new state.
   */
  private update(state: AppState): void {
    const record = state.latestTelemetry;
    // Frame and status messages also notify; engines only change with telemetry
    if (record === this.lastRecord && record !== null) return;
    this.lastRecord = record;

    if (!record) {
      this.resetAllEngines();
      return;
    }

    // Update Super Heavy engines
    for (const [key, circleGroup] of this.superheavyCircles) {
      const status: EngineStatus = record.superheavy_engines[key] ?? 'undetected';
      this.updateCircleStatus(circleGroup, status);
    }

    // Update Starship engines
    for (const [key, circleGroup] of this.starshipCircles) {
      const status: EngineStatus = record.starship_engines[key] ?? 'undetected';
      this.updateCircleStatus(circleGroup, status);
    }
  }

  /**
   * Update the fill/stroke of a circle group based on engine status.
   */
  private updateCircleStatus(group: SVGGElement, status: EngineStatus): void {
    if (this.renderedStatus.get(group) === status) return;
    const circle = group.querySelector('circle');
    if (circle) {
      circle.setAttribute('fill', getStatusFill(status));
      circle.setAttribute('stroke', getStatusStroke(status));
      this.renderedStatus.set(group, status);
    }
  }

  /**
   * Reset all engines to undetected state.
   */
  private resetAllEngines(): void {
    for (const group of this.superheavyCircles.values()) {
      this.updateCircleStatus(group, 'undetected');
    }
    for (const group of this.starshipCircles.values()) {
      this.updateCircleStatus(group, 'undetected');
    }
  }

  /**
   * Cleanup subscriptions.
   */
  destroy(): void {
    if (this.unsubscribe) {
      this.unsubscribe();
      this.unsubscribe = null;
    }
  }
}
