import { isNearLiveColor, PRESET_COLORS } from './flight-colors';
import { bindDismiss } from './ui/popover';

/** What the popover edits: one flight's colour and how to change it. */
export interface ColorChoice {
  /** Shown as the popover heading, e.g. the flight name. */
  title: string;
  color: string;
  isCustom: boolean;
  onPick: (hex: string) => void;
  onReset: () => void;
}

/**
 * Round colour swatch that opens the colour popover. Sits next to a flight's
 * checkbox rather than inside its label, so clicking it never toggles the flight.
 */
export function createSwatchButton(className: string, title: string, color: string): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `color-swatch-btn ${className}`;
  button.style.backgroundColor = color;
  button.title = `Change colour of ${title}`;
  button.setAttribute('aria-label', `Change colour of ${title}`);
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');
  return button;
}

/** Shared popover: only one flight's colour is edited at a time. */
class ColorPopover {
  private readonly panel: HTMLElement;
  private readonly heading: HTMLElement;
  private readonly presets: HTMLButtonElement[] = [];
  private readonly customInput: HTMLInputElement;
  private readonly warning: HTMLElement;
  private readonly resetButton: HTMLButtonElement;
  private anchor: HTMLElement | null = null;
  private choice: ColorChoice | null = null;

  constructor() {
    this.panel = document.createElement('div');
    this.panel.className = 'color-popover';
    this.panel.setAttribute('role', 'dialog');
    this.panel.hidden = true;

    this.heading = document.createElement('p');
    this.heading.className = 'color-popover__title';

    const grid = document.createElement('div');
    grid.className = 'color-popover__presets';
    for (const hex of PRESET_COLORS) {
      const preset = document.createElement('button');
      preset.type = 'button';
      preset.className = 'color-popover__preset';
      preset.style.backgroundColor = hex;
      preset.dataset.color = hex;
      preset.setAttribute('aria-label', hex);
      preset.addEventListener('click', () => this.pick(hex));
      this.presets.push(preset);
      grid.appendChild(preset);
    }

    const custom = document.createElement('label');
    custom.className = 'color-popover__custom';
    const customText = document.createElement('span');
    customText.textContent = 'Custom';
    this.customInput = document.createElement('input');
    this.customInput.type = 'color';
    this.customInput.className = 'color-popover__input';
    this.customInput.addEventListener('input', () => this.pick(this.customInput.value));
    custom.appendChild(customText);
    custom.appendChild(this.customInput);

    this.warning = document.createElement('p');
    this.warning.className = 'color-popover__warning';
    this.warning.textContent = 'Close to the live flight’s orange';
    this.warning.hidden = true;

    this.resetButton = document.createElement('button');
    this.resetButton.type = 'button';
    this.resetButton.className = 'color-popover__reset';
    this.resetButton.textContent = 'Reset to default';
    this.resetButton.addEventListener('click', () => {
      if (!this.choice) return;
      this.choice.onReset();
      this.close();
    });

    this.panel.append(this.heading, grid, custom, this.warning, this.resetButton);
    document.body.appendChild(this.panel);

    bindDismiss(this.panel, {
      onDismiss: (reason) => this.close(reason === 'escape'),
      ignore: () => [this.anchor],
    });
    // Fixed positioning would leave the popover behind on scroll or resize.
    const follow = () => {
      if (this.panel.hidden || !this.anchor) return;
      if (this.anchor.isConnected) this.position(this.anchor);
      else this.close(false);
    };
    window.addEventListener('resize', follow);
    window.addEventListener('scroll', follow, true);
  }

  get isOpen(): boolean {
    return !this.panel.hidden;
  }

  toggle(anchor: HTMLElement, choice: ColorChoice): void {
    if (this.isOpen && this.anchor === anchor) {
      this.close();
      return;
    }
    this.close(false);
    this.anchor = anchor;
    this.choice = { ...choice };
    this.heading.textContent = choice.title;
    this.panel.setAttribute('aria-label', `Colour of ${choice.title}`);
    this.panel.hidden = false;
    anchor.setAttribute('aria-expanded', 'true');
    this.refresh();
    this.position(anchor);
    (this.presets.find((p) => p.getAttribute('aria-pressed') === 'true') ?? this.presets[0]).focus();
  }

  /** Closes the popover, returning focus to its swatch unless the user clicked elsewhere. */
  close(restoreFocus = true): void {
    if (this.panel.hidden) return;
    this.panel.hidden = true;
    this.anchor?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) this.anchor?.focus();
    this.anchor = null;
    this.choice = null;
  }

  private pick(hex: string): void {
    if (!this.choice) return;
    this.choice.color = hex.toLowerCase();
    this.choice.isCustom = true;
    this.choice.onPick(this.choice.color);
    this.refresh();
  }

  private refresh(): void {
    if (!this.choice) return;
    const { color, isCustom } = this.choice;
    for (const preset of this.presets) {
      preset.setAttribute('aria-pressed', String(preset.dataset.color === color.toLowerCase()));
    }
    if (this.customInput.value !== color) this.customInput.value = color;
    this.warning.hidden = !isNearLiveColor(color);
    this.resetButton.disabled = !isCustom;
  }

  /** Below the swatch, kept inside the viewport. */
  private position(anchor: HTMLElement): void {
    const margin = 8;
    const rect = anchor.getBoundingClientRect();
    const { offsetWidth: width, offsetHeight: height } = this.panel;
    const left = Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin);
    const below = rect.bottom + 6;
    const top = below + height > window.innerHeight - margin ? rect.top - height - 6 : below;
    this.panel.style.left = `${Math.max(margin, left)}px`;
    this.panel.style.top = `${Math.max(margin, top)}px`;
  }
}

let popover: ColorPopover | null = null;

/** Opens the colour popover for `choice` under `anchor`, or closes it if already open there. */
export function toggleColorPopover(anchor: HTMLElement, choice: ColorChoice): void {
  popover ??= new ColorPopover();
  popover.toggle(anchor, choice);
}
