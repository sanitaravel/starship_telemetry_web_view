import type { StateManager, AppState } from './state';
import type { FramePayload } from './types';

/**
 * Frame Preview component.
 * Displays the current frame being processed by the pipeline as a live image.
 */
export class FramePreview {
  private container: HTMLElement;
  private stateManager: StateManager;
  private imgElement!: HTMLImageElement;
  private sequenceLabel!: HTMLElement;
  private unsubscribe: (() => void) | null = null;

  constructor(container: HTMLElement, stateManager: StateManager) {
    this.container = container;
    this.stateManager = stateManager;
    this.render();
    this.unsubscribe = this.stateManager.subscribe((state) => this.update(state));
  }

  private render(): void {
    this.container.innerHTML = `
      <div class="frame-preview">
        <div class="frame-preview__header">
          <span class="frame-preview__title">Live Frame</span>
          <span class="frame-preview__stats">
            <span class="frame-preview__fps"></span>
            <span class="frame-preview__seq"></span>
          </span>
        </div>
        <div class="frame-preview__image-container">
          <img class="frame-preview__img" alt="Current processed frame" />
          <div class="frame-preview__placeholder">No frame data</div>
        </div>
      </div>
    `;

    this.imgElement = this.container.querySelector('.frame-preview__img') as HTMLImageElement;
    this.sequenceLabel = this.container.querySelector('.frame-preview__seq') as HTMLElement;
  }

  private update(state: AppState): void {
    const frame = state.latestFrame;
    if (frame && frame.image) {
      this.imgElement.src = `data:image/jpeg;base64,${frame.image}`;
      this.imgElement.style.display = 'block';
      const placeholder = this.container.querySelector('.frame-preview__placeholder') as HTMLElement;
      if (placeholder) placeholder.style.display = 'none';
      this.sequenceLabel.textContent = `#${frame.sequence}`;

      const fpsEl = this.container.querySelector('.frame-preview__fps') as HTMLElement;
      if (fpsEl && frame.processing_fps) {
        fpsEl.textContent = `${frame.processing_fps.toFixed(1)} FPS`;
      }
    }
  }

  destroy(): void {
    if (this.unsubscribe) {
      this.unsubscribe();
      this.unsubscribe = null;
    }
  }
}
