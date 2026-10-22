import { downloadText, telemetryFilename, toFlightRecords } from '../domain/downloads';
import type { AppState, StateManager } from '../state/state-manager';

/**
 * Download buttons for the OCR telemetry received this session: as a flight
 * file in the `results_previous/` format (loadable as a previous flight), or
 * as the raw records exactly as the backend sent them.
 */
export class TelemetryDownloads {
  private readonly count: HTMLElement;
  private readonly buttons: HTMLButtonElement[];
  private shownCount = -1;

  constructor(container: HTMLElement, private readonly stateManager: StateManager) {
    container.classList.add('telemetry-downloads');
    container.innerHTML = `
      <span class="telemetry-downloads__label">OCR data:</span>
      <button type="button" class="telemetry-downloads__btn" data-format="flight"
        title="Speed, altitude and engines per frame in the results_previous/ format">&#8595; Flight JSON</button>
      <button type="button" class="telemetry-downloads__btn" data-format="raw"
        title="Every record exactly as received from the backend">&#8595; Raw records</button>
      <span class="telemetry-downloads__count" aria-live="polite"></span>
    `;
    this.count = container.querySelector('.telemetry-downloads__count')!;
    this.buttons = [...container.querySelectorAll<HTMLButtonElement>('.telemetry-downloads__btn')];
    container.addEventListener('click', (e) => {
      const format = (e.target as HTMLElement).closest<HTMLElement>('[data-format]')?.dataset.format;
      if (format === 'flight' || format === 'raw') this.download(format);
    });

    this.update(stateManager.getState());
    stateManager.subscribe((state) => this.update(state));
  }

  private download(format: 'flight' | 'raw'): void {
    const records = this.stateManager.getState().telemetryRecords;
    if (records.length === 0) return;
    const content =
      format === 'flight'
        ? JSON.stringify(toFlightRecords(records), null, 4)
        : JSON.stringify(records, null, 2);
    downloadText(telemetryFilename(new Date(), format === 'raw' ? '-raw' : ''), content, 'application/json');
  }

  private update(state: AppState): void {
    const n = state.telemetryRecords.length;
    if (n === this.shownCount) return;
    this.shownCount = n;
    this.count.textContent = `${n.toLocaleString('en-US')} record${n === 1 ? '' : 's'}`;
    for (const button of this.buttons) button.disabled = n === 0;
  }
}
