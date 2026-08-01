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
 * Renders mission elapsed time, speed/altitude values with units,
 * and stage labels for both left and right vehicle stages.
 * Subscribes to state updates and re-renders when new telemetry arrives.
 */
export class TelemetryDisplay {
  private container: HTMLElement;
  private stateManager: StateManager;

  private metElement!: HTMLElement;
  private leftStageLabel!: HTMLElement;
  private rightStageLabel!: HTMLElement;
  private leftSpeed!: HTMLElement;
  private rightSpeed!: HTMLElement;
  private leftAltitude!: HTMLElement;
  private rightAltitude!: HTMLElement;

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

    const metLabel = document.createElement('span');
    metLabel.className = 'telemetry-display__met-label';
    metLabel.textContent = 'T+';

    this.metElement = document.createElement('span');
    this.metElement.className = 'telemetry-display__met-value';
    this.metElement.setAttribute('aria-label', 'Mission Elapsed Time');
    this.metElement.textContent = '--:--:--';

    metSection.appendChild(metLabel);
    metSection.appendChild(this.metElement);

    // Vehicle telemetry grid
    const grid = document.createElement('div');
    grid.className = 'telemetry-display__grid';

    // Left stage column
    const leftCol = document.createElement('div');
    leftCol.className = 'telemetry-display__column';

    this.leftStageLabel = document.createElement('div');
    this.leftStageLabel.className = 'telemetry-display__stage-label';
    this.leftStageLabel.textContent = '--';

    const leftSpeedRow = this.createFieldRow('SPD');
    this.leftSpeed = leftSpeedRow.querySelector('.telemetry-display__field-value')!;

    const leftAltRow = this.createFieldRow('ALT');
    this.leftAltitude = leftAltRow.querySelector('.telemetry-display__field-value')!;

    leftCol.appendChild(this.leftStageLabel);
    leftCol.appendChild(leftSpeedRow);
    leftCol.appendChild(leftAltRow);

    // Right stage column
    const rightCol = document.createElement('div');
    rightCol.className = 'telemetry-display__column';

    this.rightStageLabel = document.createElement('div');
    this.rightStageLabel.className = 'telemetry-display__stage-label';
    this.rightStageLabel.textContent = '--';

    const rightSpeedRow = this.createFieldRow('SPD');
    this.rightSpeed = rightSpeedRow.querySelector('.telemetry-display__field-value')!;

    const rightAltRow = this.createFieldRow('ALT');
    this.rightAltitude = rightAltRow.querySelector('.telemetry-display__field-value')!;

    rightCol.appendChild(this.rightStageLabel);
    rightCol.appendChild(rightSpeedRow);
    rightCol.appendChild(rightAltRow);

    grid.appendChild(leftCol);
    grid.appendChild(rightCol);

    this.container.appendChild(metSection);
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
    this.leftStageLabel.textContent = '--';
    this.rightStageLabel.textContent = '--';
    this.leftSpeed.textContent = '--';
    this.rightSpeed.textContent = '--';
    this.leftAltitude.textContent = '--';
    this.rightAltitude.textContent = '--';
  }

  /**
   * Render telemetry values from the latest record.
   */
  private renderTelemetry(record: TelemetryRecord): void {
    // Mission Elapsed Time
    this.metElement.textContent = record.mission_elapsed_time ?? '--:--:--';

    // Stage labels
    this.leftStageLabel.textContent = record.stage_left_label ?? '--';
    this.rightStageLabel.textContent = record.stage_right_label ?? '--';

    // Speed values
    this.leftSpeed.textContent = formatFieldValue(record.speed_left);
    this.rightSpeed.textContent = formatFieldValue(record.speed_right);

    // Altitude values
    this.leftAltitude.textContent = formatFieldValue(record.altitude_left);
    this.rightAltitude.textContent = formatFieldValue(record.altitude_right);
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
