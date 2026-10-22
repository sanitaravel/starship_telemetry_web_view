import type { StateManager, AppState } from '../state/state-manager';

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
  private placeholderElement: HTMLElement | null = null;
  private fpsElement: HTMLElement | null = null;
  private lastFrame: AppState['latestFrame'] = null;
  private lastFps = 0;

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
    this.placeholderElement = this.container.querySelector('.frame-preview__placeholder');
    this.fpsElement = this.container.querySelector('.frame-preview__fps');
  }

  private update(state: AppState): void {
    const frame = state.latestFrame;
    // Telemetry and status messages also notify; only redraw on a new frame
    // or FPS reading, since decoding a new image source is the costly part.
    if (frame === this.lastFrame && state.processingFps === this.lastFps) return;
    if (frame && frame.image) {
      if (frame !== this.lastFrame) {
        this.imgElement.src = `data:image/jpeg;base64,${frame.image}`;
        this.imgElement.style.display = 'block';
        if (this.placeholderElement) this.placeholderElement.style.display = 'none';
        this.sequenceLabel.textContent = `#${frame.sequence}`;
      }

      if (this.fpsElement) {
        const fps = state.processingFps;
        this.fpsElement.textContent = fps > 0 ? `${fps.toFixed(1)} FPS` : '';
      }
      this.lastFrame = frame;
      this.lastFps = state.processingFps;
    }
  }

  destroy(): void {
    if (this.unsubscribe) {
      this.unsubscribe();
      this.unsubscribe = null;
    }
  }
}
