/**
 * The one tooltip both engine diagrams share: hovering an engine with a
 * serial shows it straight away, above the engine's circle.
 */
export class EngineTooltip {
  private readonly tooltip: HTMLElement;
  private readonly tooltipLabel: HTMLElement;
  private readonly tooltipSerial: HTMLElement;
  private hoveredEngine: SVGGElement | null = null;

  constructor() {
    this.tooltip = document.createElement('div');
    this.tooltip.className = 'engine-tooltip';
    this.tooltip.setAttribute('role', 'tooltip');
    this.tooltip.hidden = true;
    this.tooltipLabel = document.createElement('span');
    this.tooltipLabel.className = 'engine-tooltip__engine';
    this.tooltipSerial = document.createElement('span');
    this.tooltipSerial.className = 'engine-tooltip__serial';
    this.tooltip.append(this.tooltipLabel, this.tooltipSerial);
    document.body.appendChild(this.tooltip);
  }

  /** Hovering an engine with a serial shows it straight away. */
  attach(svg: SVGSVGElement): void {
    // Every <g> in this diagram is one engine (circle + label)
    svg.addEventListener('pointerover', (e) => {
      const group = (e.target as Element).closest('g');
      if (group) this.show(group);
      else this.hide();
    });
    svg.addEventListener('pointerleave', () => this.hide());
  }

  /** Keeps an open tooltip in step with the serial under the pointer. */
  refresh(): void {
    if (this.hoveredEngine) this.show(this.hoveredEngine);
  }

  remove(): void {
    this.tooltip.remove();
  }

  /** Shows the serial of the engine under the pointer, above its circle. */
  private show(group: SVGGElement): void {
    this.hoveredEngine = group;
    const serial = group.dataset.serial;
    if (!serial) {
      this.tooltip.hidden = true;
      return;
    }
    this.tooltipLabel.textContent = group.dataset.label!;
    this.tooltipSerial.textContent = serial;
    this.tooltip.hidden = false;

    const rect = group.querySelector('circle')!.getBoundingClientRect();
    const { offsetWidth: width, offsetHeight: height } = this.tooltip;
    const margin = 8;
    const left = rect.left + rect.width / 2 - width / 2;
    const above = rect.top - height - 6;
    const top = above < margin ? rect.bottom + 6 : above;
    this.tooltip.style.left = `${Math.min(Math.max(margin, left), window.innerWidth - width - margin)}px`;
    this.tooltip.style.top = `${top}px`;
  }

  private hide(): void {
    this.hoveredEngine = null;
    this.tooltip.hidden = true;
  }
}
