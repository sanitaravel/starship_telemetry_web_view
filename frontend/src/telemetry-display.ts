import type { StateManager, AppState } from './state';
import type { TelemetryRecord } from './types';

/**
 * Determines if a telemetry field value should display a placeholder.
 * Returns true when the field status is "unavailable" or "occluded_by_engines",
 * or when the value itself is null.
 */
function shouldShowPlaceholder(field: { value: number | null; unit: string | null; status: string }): boolean {
  return field.value === null || field.status === 'unavailable' || field.status === 'occluded_by_engines';
}

/**
 * Formats a telemetry field value with its unit, or returns "--" placeholder.
 */
function formatFieldValue(field: { value: number | null; unit: string | null; status: string }): string {
  if (shouldShowPlaceholder(field)) {
    return '--';
  }
  const unit = field.unit ?? '';
  return `${field.value}${unit ? ' ' + unit : ''}`;
}

/**
 * Telemetry Display UI component.
 * Renders mission elapsed time and speed/altitude values with units
 * for left (L) and right (R) sides of the telemetry overlay.
 * Subscribes to state updates and re-renders when new telemetry arrives.
 */
export class TelemetryDisplay {
  private container: HTMLElement;
  private stateManager: StateManager;

  private metElement!: HTMLElement;
  private tZeroIndicator!: HTMLElement;
  private tZeroDot!: HTMLElement;
  private stageSepIndicator!: HTMLElement;
  private stageSepDot!: HTMLElement;
  private speedL!: HTMLElement;
  private speedR!: HTMLElement;
  private altitudeL!: HTMLElement;
  private altitudeR!: HTMLElement;

  private unsubscribe: (() => void) | null = null;

  constructor(container: HTMLElement, stateManager: StateManager) {
    this.container = container;
    this.stateManager = stateManager;

    this.render();
    this.subscribeToState();
    this.update(this.stateManager.getState());
  }

  /**
   * Build the DOM structure for the telemetry display.
   */
  private render(): void {
    this.container.innerHTML = '';
    this.container.classList.add('telemetry-display');

    // Mission Elapsed Time
    const metSection = document.createElement('div');
    metSection.className = 'telemetry-display__met';

    this.metElement = document.createElement('span');
    this.metElement.className = 'telemetry-display__met-value';
    this.metElement.setAttribute('aria-label', 'Mission Elapsed Time');
    this.metElement.textContent = '--:--:--';

    metSection.appendChild(this.metElement);

    // Status indicators for T-0 and Stage Separation
    const indicatorsSection = document.createElement('div');
    indicatorsSection.className = 'telemetry-display__indicators';

    const tZeroBadge = document.createElement('span');
    tZeroBadge.className = 'telemetry-display__badge telemetry-display__badge--inactive';
    this.tZeroIndicator = tZeroBadge;

    this.tZeroDot = document.createElement('span');
    this.tZeroDot.className = 'telemetry-display__badge-dot';

    const tZeroLabel = document.createElement('span');
    tZeroLabel.className = 'telemetry-display__badge-label';
    tZeroLabel.textContent = 'T-0';

    tZeroBadge.setAttribute('aria-label', 'T-0 Detection Status');
    tZeroBadge.appendChild(this.tZeroDot);
    tZeroBadge.appendChild(tZeroLabel);

    const stageSepBadge = document.createElement('span');
    stageSepBadge.className = 'telemetry-display__badge telemetry-display__badge--inactive';
    this.stageSepIndicator = stageSepBadge;

    this.stageSepDot = document.createElement('span');
    this.stageSepDot.className = 'telemetry-display__badge-dot';

    const stageSepLabel = document.createElement('span');
    stageSepLabel.className = 'telemetry-display__badge-label';
    stageSepLabel.textContent = 'STAGE SEP';

    stageSepBadge.setAttribute('aria-label', 'Stage Separation Detection Status');
    stageSepBadge.appendChild(this.stageSepDot);
    stageSepBadge.appendChild(stageSepLabel);

    indicatorsSection.appendChild(tZeroBadge);
    indicatorsSection.appendChild(stageSepBadge);

    // Telemetry grid: L and R columns
    const grid = document.createElement('div');
    grid.className = 'telemetry-display__grid';

    // Left (L) column
    const leftCol = document.createElement('div');
    leftCol.className = 'telemetry-display__column';

    const leftHeader = document.createElement('div');
    leftHeader.className = 'telemetry-display__column-header';
    leftHeader.textContent = 'L';

    const leftSpeedRow = this.createFieldRow('SPD');
    this.speedL = leftSpeedRow.querySelector('.telemetry-display__field-value')!;

    const leftAltRow = this.createFieldRow('ALT');
    this.altitudeL = leftAltRow.querySelector('.telemetry-display__field-value')!;

    leftCol.appendChild(leftHeader);
    leftCol.appendChild(leftSpeedRow);
    leftCol.appendChild(leftAltRow);

    // Right (R) column
    const rightCol = document.createElement('div');
    rightCol.className = 'telemetry-display__column';

    const rightHeader = document.createElement('div');
    rightHeader.className = 'telemetry-display__column-header';
    rightHeader.textContent = 'R';

    const rightSpeedRow = this.createFieldRow('SPD');
    this.speedR = rightSpeedRow.querySelector('.telemetry-display__field-value')!;

    const rightAltRow = this.createFieldRow('ALT');
    this.altitudeR = rightAltRow.querySelector('.telemetry-display__field-value')!;

    rightCol.appendChild(rightHeader);
    rightCol.appendChild(rightSpeedRow);
    rightCol.appendChild(rightAltRow);

    grid.appendChild(leftCol);
    grid.appendChild(rightCol);

    this.container.appendChild(metSection);
    this.container.appendChild(indicatorsSection);
    this.container.appendChild(grid);
  }

  /**
   * Create a labeled field row with a label and value element.
   */
  private createFieldRow(label: string): HTMLElement {
    const row = document.createElement('div');
    row.className = 'telemetry-display__field-row';

    const labelEl = document.createElement('span');
    labelEl.className = 'telemetry-display__field-label';
    labelEl.textContent = label;

    const valueEl = document.createElement('span');
    valueEl.className = 'telemetry-display__field-value';
    valueEl.textContent = '--';

    row.appendChild(labelEl);
    row.appendChild(valueEl);
    return row;
  }

  /**
   * Subscribe to StateManager for telemetry updates.
   */
  private subscribeToState(): void {
    this.unsubscribe = this.stateManager.subscribe((state) => this.update(state));
  }

  /**
   * Update UI based on new application state.
   */
  private update(state: AppState): void {
    const record = state.latestTelemetry;

    if (!record) {
      this.renderEmpty();
      return;
    }

    this.renderTelemetry(record);
  }

  /**
   * Render placeholder state when no telemetry is available.
   */
  private renderEmpty(): void {
    this.metElement.textContent = '--:--:--';
    this.tZeroIndicator.className = 'telemetry-display__badge telemetry-display__badge--inactive';
    this.stageSepIndicator.className = 'telemetry-display__badge telemetry-display__badge--inactive';
    this.speedL.textContent = '--';
    this.speedR.textContent = '--';
    this.altitudeL.textContent = '--';
    this.altitudeR.textContent = '--';
  }

  /**
   * Render telemetry values from the latest record.
   */
  private renderTelemetry(record: TelemetryRecord): void {
    // Mission Elapsed Time
    this.metElement.textContent = record.mission_elapsed_time ?? '--:--:--';

    // T-0 detection badge
    this.tZeroIndicator.className = record.t_zero_found
      ? 'telemetry-display__badge telemetry-display__badge--active'
      : 'telemetry-display__badge telemetry-display__badge--inactive';

    // Stage separation badge
    this.stageSepIndicator.className = record.stage_sep_found
      ? 'telemetry-display__badge telemetry-display__badge--active'
      : 'telemetry-display__badge telemetry-display__badge--inactive';

    // Speed values with units
    this.speedL.textContent = formatFieldValue(record.speed_left);
    this.speedR.textContent = formatFieldValue(record.speed_right);

    // Altitude values with units
    this.altitudeL.textContent = formatFieldValue(record.altitude_left);
    this.altitudeR.textContent = formatFieldValue(record.altitude_right);
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
