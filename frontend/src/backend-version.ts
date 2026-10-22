import type { StateManager } from './state/state-manager';
import { fetchPipelineStatus } from './api/status';
import { appVersion } from './core/app-version';
import { createLogger } from './core/logger';

const logger = createLogger('backend-version');

/**
 * Shows the version of the backend the page is talking to, next to the
 * page's own version, and flags a mismatch: an old backend left running
 * serves old code while the header shows the new frontend version.
 *
 * The version comes from GET /api/status on load and from every status
 * message after that.
 */
export class BackendVersion {
  private element: HTMLElement;
  private shown: string | null = null;

  constructor(element: HTMLElement, stateManager: StateManager) {
    this.element = element;
    this.render(null);
    stateManager.subscribe((state) => {
      const version = state.pipelineStatus?.version;
      if (version) this.render(version);
    });
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const status = await fetchPipelineStatus();
      if (status === undefined) return;
      if (status.version) this.render(status.version);
    } catch (err) {
      logger.warn('Could not fetch backend status', { error: String(err) });
    }
  }

  private render(version: string | null): void {
    if (version === this.shown && version !== null) return;
    this.shown = version;
    const mismatch = version !== null && version !== appVersion();
    this.element.textContent = `backend ${version ? `v${version}` : 'unknown'}`;
    this.element.classList.toggle('dashboard-header__version--mismatch', mismatch);
    this.element.title = mismatch
      ? `The backend runs v${version}, this page is v${appVersion()}. ` +
        'Restart the backend (an old one may still be running) or reload the page.'
      : 'Version of the backend this page is connected to';
  }
}
