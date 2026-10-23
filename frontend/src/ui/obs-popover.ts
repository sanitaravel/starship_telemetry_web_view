import type { OverlayWidgetId, OverlayWidgetInfo } from '../domain/overlay/widgets';
import { anchorPanel, bindDismiss, type AnchoredPanel } from './popover';

/** What the OBS popover offers for one panel. */
export interface ObsChoice {
  /** Shown as the popover heading, e.g. "Time-Series". */
  title: string;
  /** The panel's overlay widgets; a select is shown when there is more than one. */
  widgets: readonly OverlayWidgetInfo[];
  /** The widget selected when the popover opens. */
  selected: OverlayWidgetId;
  /** The overlay URL for a widget, built from the panel's settings at call time. */
  urlFor(widget: OverlayWidgetId): string;
}

/** The "OBS" button that opens the OBS popover for a panel. */
export function createObsButton(panelName: string): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'obs-btn';
  button.textContent = 'OBS';
  button.title = `OBS Browser Source URL for ${panelName}`;
  button.setAttribute('aria-label', `OBS Browser Source URL for ${panelName}`);
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');
  return button;
}

/** How long the Copy button says "Copied" before it reads "Copy" again. */
const COPIED_MS = 1500;

/** Shared popover: one panel's overlay URL at a time. */
class ObsPopover {
  private readonly panel: HTMLElement;
  private readonly heading: HTMLElement;
  private readonly selectLabel: HTMLElement;
  private readonly select: HTMLSelectElement;
  private readonly urlInput: HTMLInputElement;
  private readonly copyButton: HTMLButtonElement;
  private readonly status: HTMLElement;
  private readonly size: HTMLElement;
  private readonly anchored: AnchoredPanel;
  private anchor: HTMLElement | null = null;
  private choice: ObsChoice | null = null;
  private copiedTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    this.panel = document.createElement('div');
    this.panel.className = 'obs-popover';
    this.panel.setAttribute('role', 'dialog');
    this.panel.hidden = true;

    this.heading = document.createElement('p');
    this.heading.className = 'obs-popover__title';

    this.selectLabel = document.createElement('label');
    this.selectLabel.className = 'obs-popover__field';
    const selectText = document.createElement('span');
    selectText.textContent = 'Widget';
    this.select = document.createElement('select');
    this.select.className = 'obs-popover__select';
    this.select.addEventListener('change', () => this.refresh());
    this.selectLabel.append(selectText, this.select);

    const urlLabel = document.createElement('label');
    urlLabel.className = 'obs-popover__field';
    const urlText = document.createElement('span');
    urlText.textContent = 'Browser Source URL';
    this.urlInput = document.createElement('input');
    this.urlInput.type = 'text';
    this.urlInput.readOnly = true;
    this.urlInput.className = 'obs-popover__url';
    this.urlInput.spellcheck = false;
    this.urlInput.addEventListener('focus', () => this.urlInput.select());
    urlLabel.append(urlText, this.urlInput);

    const row = document.createElement('div');
    row.className = 'obs-popover__row';
    this.copyButton = document.createElement('button');
    this.copyButton.type = 'button';
    this.copyButton.className = 'obs-popover__copy';
    this.copyButton.textContent = 'Copy';
    this.copyButton.addEventListener('click', () => void this.copy());
    this.size = document.createElement('span');
    this.size.className = 'obs-popover__size';
    row.append(this.copyButton, this.size);

    this.status = document.createElement('p');
    this.status.className = 'obs-popover__status';
    this.status.setAttribute('role', 'status');

    const note = document.createElement('p');
    note.className = 'obs-popover__note';
    note.textContent = 'Copy again after changing settings.';

    this.panel.append(this.heading, this.selectLabel, urlLabel, row, this.status, note);
    document.body.appendChild(this.panel);

    bindDismiss(this.panel, {
      onDismiss: (reason) => this.close(reason === 'escape'),
      ignore: () => [this.anchor],
    });
    // Below the button, lined up with its right edge (the buttons sit at panel corners)
    this.anchored = anchorPanel(this.panel, () => this.anchor, {
      align: 'right',
      onAnchorRemoved: () => this.close(false),
    });
  }

  get isOpen(): boolean {
    return !this.panel.hidden;
  }

  toggle(anchor: HTMLElement, choice: ObsChoice): void {
    if (this.isOpen && this.anchor === anchor) {
      this.close();
      return;
    }
    this.close(false);
    this.anchor = anchor;
    this.choice = choice;
    this.heading.textContent = `OBS overlay: ${choice.title}`;
    this.panel.setAttribute('aria-label', `OBS overlay for ${choice.title}`);
    this.select.replaceChildren(
      ...choice.widgets.map((w) => {
        const option = document.createElement('option');
        option.value = w.id;
        option.textContent = w.label;
        return option;
      }),
    );
    this.select.value = choice.selected;
    this.selectLabel.hidden = choice.widgets.length < 2;
    this.panel.hidden = false;
    anchor.setAttribute('aria-expanded', 'true');
    this.refresh();
    this.anchored.reposition();
    this.copyButton.focus();
  }

  /** Closes the popover, returning focus to its button unless the user clicked elsewhere. */
  close(restoreFocus = true): void {
    if (this.panel.hidden) return;
    this.panel.hidden = true;
    this.anchor?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) this.anchor?.focus();
    this.anchor = null;
    this.choice = null;
    this.resetCopyLabel();
  }

  /** Rebuilds the URL and size for the selected widget from the panel's current settings. */
  private refresh(): void {
    if (!this.choice) return;
    const widget = this.choice.widgets.find((w) => w.id === this.select.value) ?? this.choice.widgets[0];
    this.urlInput.value = this.choice.urlFor(widget.id);
    this.size.textContent = `Suggested size ${widget.size.width} × ${widget.size.height}`;
    this.status.textContent = '';
    this.resetCopyLabel();
  }

  /** Copies the URL; without a usable clipboard, selects it for Ctrl+C. */
  private async copy(): Promise<void> {
    const url = this.urlInput.value;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard API unavailable');
      await navigator.clipboard.writeText(url);
    } catch {
      this.urlInput.focus();
      this.urlInput.select();
      this.status.textContent = 'Press Ctrl+C to copy.';
      return;
    }
    this.status.textContent = '';
    this.copyButton.textContent = 'Copied';
    if (this.copiedTimer) clearTimeout(this.copiedTimer);
    this.copiedTimer = setTimeout(() => this.resetCopyLabel(), COPIED_MS);
  }

  private resetCopyLabel(): void {
    if (this.copiedTimer) clearTimeout(this.copiedTimer);
    this.copiedTimer = null;
    this.copyButton.textContent = 'Copy';
  }
}

let popover: ObsPopover | null = null;

/** Opens the OBS popover for `choice` under `anchor`, or closes it if already open there. */
export function toggleObsPopover(anchor: HTMLElement, choice: ObsChoice): void {
  popover ??= new ObsPopover();
  popover.toggle(anchor, choice);
}
