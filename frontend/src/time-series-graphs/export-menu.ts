import {
  DEFAULT_EXPORT_OPTIONS,
  FONT_SIZE_CHOICES,
  type ExportBackground,
  type ExportOptions,
} from './export-png';

const STORAGE_KEY = 'time-series-graphs.export-options';

const BACKGROUND_CHOICES: { value: ExportBackground; label: string }[] = [
  { value: 'dark', label: 'Dark' },
  { value: 'white', label: 'White' },
  { value: 'transparent', label: 'Transparent' },
];

/** Read saved export options, falling back to defaults for anything missing or invalid. */
export function loadExportOptions(): ExportOptions {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_EXPORT_OPTIONS };
    const saved = JSON.parse(raw) as Partial<ExportOptions>;
    return {
      fontSize: FONT_SIZE_CHOICES.includes(saved.fontSize as number)
        ? (saved.fontSize as number)
        : DEFAULT_EXPORT_OPTIONS.fontSize,
      background: BACKGROUND_CHOICES.some((c) => c.value === saved.background)
        ? (saved.background as ExportBackground)
        : DEFAULT_EXPORT_OPTIONS.background,
    };
  } catch {
    // Storage blocked (private mode, disabled site data) or corrupt JSON
    return { ...DEFAULT_EXPORT_OPTIONS };
  }
}

function saveExportOptions(options: ExportOptions): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(options));
  } catch {
    // Not persisting is fine; the menu still works for this page view
  }
}

let nextMenuId = 0;

/**
 * "Save PNG" button with a popover for export settings (font size and
 * background). Choices persist across page loads; the popover closes on
 * Escape, on a click outside it, and after a download.
 */
export class ExportMenu {
  readonly element: HTMLElement;
  private trigger: HTMLButtonElement;
  private panel: HTMLElement;
  private fontSelect: HTMLSelectElement;
  private options: ExportOptions;

  constructor(private onDownload: (options: ExportOptions) => void) {
    this.options = loadExportOptions();
    const id = `time-series-graphs-export-${nextMenuId++}`;

    this.element = document.createElement('div');
    this.element.className = 'time-series-graphs__export';

    this.trigger = document.createElement('button');
    this.trigger.type = 'button';
    this.trigger.className = 'time-series-graphs__save-btn';
    this.trigger.textContent = 'Save PNG ▾';
    this.trigger.setAttribute('aria-label', 'Save chart as PNG image');
    this.trigger.setAttribute('aria-haspopup', 'dialog');
    this.trigger.setAttribute('aria-expanded', 'false');
    this.trigger.setAttribute('aria-controls', id);
    this.trigger.addEventListener('click', () => this.setOpen(this.panel.hidden));

    this.panel = document.createElement('div');
    this.panel.id = id;
    this.panel.className = 'time-series-graphs__export-panel';
    this.panel.setAttribute('role', 'dialog');
    this.panel.setAttribute('aria-label', 'PNG export options');
    this.panel.hidden = true;

    // Font size
    const fontField = document.createElement('label');
    fontField.className = 'time-series-graphs__export-field';
    const fontLabel = document.createElement('span');
    fontLabel.className = 'time-series-graphs__export-label';
    fontLabel.textContent = 'Font size';
    this.fontSelect = document.createElement('select');
    this.fontSelect.className = 'time-series-graphs__select time-series-graphs__export-font';
    for (const size of FONT_SIZE_CHOICES) {
      const opt = document.createElement('option');
      opt.value = String(size);
      opt.textContent = size === DEFAULT_EXPORT_OPTIONS.fontSize ? `${size} px (default)` : `${size} px`;
      this.fontSelect.appendChild(opt);
    }
    this.fontSelect.value = String(this.options.fontSize);
    this.fontSelect.addEventListener('change', () => {
      this.update({ fontSize: Number(this.fontSelect.value) });
    });
    fontField.appendChild(fontLabel);
    fontField.appendChild(this.fontSelect);

    // Background (segmented radio group)
    const bgField = document.createElement('fieldset');
    bgField.className = 'time-series-graphs__export-field';
    const bgLegend = document.createElement('legend');
    bgLegend.className = 'time-series-graphs__export-label';
    bgLegend.textContent = 'Background';
    const segments = document.createElement('div');
    segments.className = 'time-series-graphs__export-segments';
    for (const choice of BACKGROUND_CHOICES) {
      const segment = document.createElement('label');
      segment.className = 'time-series-graphs__export-segment';
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = `${id}-background`;
      radio.value = choice.value;
      radio.checked = choice.value === this.options.background;
      radio.addEventListener('change', () => {
        if (radio.checked) this.update({ background: choice.value });
      });
      const swatch = document.createElement('span');
      swatch.className = `time-series-graphs__export-swatch time-series-graphs__export-swatch--${choice.value}`;
      swatch.setAttribute('aria-hidden', 'true');
      const text = document.createElement('span');
      text.textContent = choice.label;
      segment.appendChild(radio);
      segment.appendChild(swatch);
      segment.appendChild(text);
      segments.appendChild(segment);
    }
    bgField.appendChild(bgLegend);
    bgField.appendChild(segments);

    const download = document.createElement('button');
    download.type = 'button';
    download.className = 'time-series-graphs__export-download';
    download.textContent = 'Download PNG';
    download.addEventListener('click', () => {
      this.onDownload({ ...this.options });
      this.setOpen(false);
      this.trigger.focus();
    });

    this.panel.appendChild(fontField);
    this.panel.appendChild(bgField);
    this.panel.appendChild(download);
    this.element.appendChild(this.trigger);
    this.element.appendChild(this.panel);

    this.panel.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        this.setOpen(false);
        this.trigger.focus();
      }
    });
    document.addEventListener('pointerdown', (e) => {
      if (!this.panel.hidden && !this.element.contains(e.target as Node)) {
        this.setOpen(false);
      }
    });
  }

  get isOpen(): boolean {
    return !this.panel.hidden;
  }

  private setOpen(open: boolean): void {
    this.panel.hidden = !open;
    this.trigger.setAttribute('aria-expanded', String(open));
    if (open) this.fontSelect.focus();
  }

  private update(change: Partial<ExportOptions>): void {
    this.options = { ...this.options, ...change };
    saveExportOptions(this.options);
  }
}
