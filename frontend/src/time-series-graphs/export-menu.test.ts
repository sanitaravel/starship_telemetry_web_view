/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ExportMenu, loadExportOptions } from './export-menu';
import { DEFAULT_EXPORT_OPTIONS } from './export-png';

const STORAGE_KEY = 'time-series-graphs.export-options';

function mount(onDownload = vi.fn()) {
  const menu = new ExportMenu(onDownload);
  document.body.appendChild(menu.element);
  const q = <T extends Element>(sel: string) => menu.element.querySelector(sel) as unknown as T;
  return {
    menu,
    onDownload,
    trigger: q<HTMLButtonElement>('.time-series-graphs__save-btn'),
    panel: q<HTMLElement>('.time-series-graphs__export-panel'),
    font: q<HTMLSelectElement>('.time-series-graphs__export-font'),
    radio: (value: string) => q<HTMLInputElement>(`input[value="${value}"]`),
    download: q<HTMLButtonElement>('.time-series-graphs__export-download'),
  };
}

function choose(el: HTMLSelectElement | HTMLInputElement, value?: string): void {
  if (el instanceof HTMLSelectElement) el.value = value!;
  else el.checked = true;
  el.dispatchEvent(new Event('change'));
}

describe('ExportMenu', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('starts closed and toggles open from the Save PNG button', () => {
    const { menu, trigger, panel, font } = mount();
    expect(menu.isOpen).toBe(false);
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(trigger.getAttribute('aria-controls')).toBe(panel.id);

    trigger.click();
    expect(menu.isOpen).toBe(true);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(font);

    trigger.click();
    expect(menu.isOpen).toBe(false);
  });

  it('offers the font sizes and the three backgrounds, defaulting to 10 px dark', () => {
    const { font, radio } = mount();
    expect([...font.options].map((o) => o.value)).toEqual(['10', '12', '14', '16', '20', '24']);
    expect(font.value).toBe('10');
    expect(radio('dark').checked).toBe(true);
    expect(radio('white').checked).toBe(false);
    expect(radio('transparent').checked).toBe(false);
  });

  it('downloads with the chosen options and closes, returning focus to the button', () => {
    const { menu, onDownload, trigger, font, radio, download } = mount();
    trigger.click();
    choose(font, '16');
    choose(radio('transparent'));
    download.click();

    expect(onDownload).toHaveBeenCalledWith({ fontSize: 16, background: 'transparent' });
    expect(menu.isOpen).toBe(false);
    expect(document.activeElement).toBe(trigger);
  });

  it('closes on Escape and on a click outside, but not on a click inside', () => {
    const { menu, trigger, panel, font } = mount();
    trigger.click();
    font.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(menu.isOpen).toBe(false);
    expect(document.activeElement).toBe(trigger);

    trigger.click();
    panel.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(menu.isOpen).toBe(true);
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(menu.isOpen).toBe(false);
  });

  it('remembers the choices for the next page load', () => {
    const first = mount();
    choose(first.font, '14');
    choose(first.radio('white'));

    document.body.innerHTML = '';
    const second = mount();
    expect(second.font.value).toBe('14');
    expect(second.radio('white').checked).toBe(true);
  });
});

describe('loadExportOptions', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it('falls back to defaults for invalid saved values', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ fontSize: 99, background: 'neon' }));
    expect(loadExportOptions()).toEqual(DEFAULT_EXPORT_OPTIONS);
  });

  it('falls back to defaults for corrupt JSON', () => {
    localStorage.setItem(STORAGE_KEY, '{not json');
    expect(loadExportOptions()).toEqual(DEFAULT_EXPORT_OPTIONS);
  });

  it('falls back to defaults when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    expect(loadExportOptions()).toEqual(DEFAULT_EXPORT_OPTIONS);
  });
});
