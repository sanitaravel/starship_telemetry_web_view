import type { StateManager, AppState } from '../state/state-manager';

/** Shown until a record with a MET arrives, as in the telemetry display. */
const NO_MET = '--:--:--';

/**
 * Mission elapsed time on its own (the `met` overlay): the latest record's
 * MET as the telemetry display shows it, nothing else.
 */
export class MetDisplay {
  private readonly value: HTMLElement;
  private lastRecord: AppState['latestTelemetry'] = null;
  private unsubscribe: (() => void) | null;

  constructor(container: HTMLElement, stateManager: StateManager) {
    container.innerHTML = '';
    container.classList.add('met-display');

    const label = document.createElement('span');
    label.className = 'met-display__label';
    label.textContent = 'MET';

    this.value = document.createElement('span');
    this.value.className = 'met-display__value';
    this.value.setAttribute('aria-label', 'Mission Elapsed Time');
    this.value.textContent = NO_MET;

    container.append(label, this.value);
    this.unsubscribe = stateManager.subscribe((state) => this.update(state));
    this.update(stateManager.getState());
  }

  private update(state: AppState): void {
    const record = state.latestTelemetry;
    // Frame and status messages also notify; skip them
    if (record === this.lastRecord && record !== null) return;
    this.lastRecord = record;
    this.value.textContent = record?.mission_elapsed_time ?? NO_MET;
  }

  destroy(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
  }
}
