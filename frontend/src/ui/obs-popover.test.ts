/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from 'vitest';
import type { ObsChoice } from './obs-popover';
import { OVERLAY_WIDGETS, type OverlayWidgetId } from '../domain/overlay/widgets';

// The OBS popover is a module-level singleton and workers are shared
// (isolate: false): feature tests open it through their own imports. Load
// a private copy of the module and drop it (and its panel) afterwards.
let createObsButton: typeof import('./obs-popover').createObsButton;
let toggleObsPopover: typeof import('./obs-popover').toggleObsPopover;

let ownPanel: HTMLElement | undefined;

beforeAll(async () => {
  document.dispatchEvent(new Event('pointerdown')); // closes another copy's popover left open
  vi.resetModules();
  ({ createObsButton, toggleObsPopover } = await import('./obs-popover'));
});

afterAll(() => {
  document.dispatchEvent(new Event('pointerdown'));
  ownPanel?.remove();
  vi.resetModules();
});

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

afterEach(() => {
  document.dispatchEvent(new Event('pointerdown'));
  document.querySelectorAll('.test-obs').forEach((el) => el.remove());
  if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
  else delete (navigator as unknown as Record<string, unknown>).clipboard;
});

function setClipboard(writeText: ((text: string) => Promise<void>) | undefined) {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: writeText ? { writeText } : undefined,
  });
}

const TELEMETRY_WIDGETS = OVERLAY_WIDGETS.filter((w) => w.id === 'telemetry' || w.id === 'met');

function setup(overrides: Partial<ObsChoice> = {}) {
  const button = createObsButton('Telemetry');
  button.classList.add('test-obs');
  document.body.appendChild(button);
  const urlFor = vi.fn((id: OverlayWidgetId) => `http://localhost:5173/?overlay=${id}&s=eyJ2IjoxfQ`);
  const choice: ObsChoice = { title: 'Telemetry', widgets: TELEMETRY_WIDGETS, selected: 'telemetry', urlFor, ...overrides };
  toggleObsPopover(button, choice);
  const panel = [...document.querySelectorAll<HTMLElement>('.obs-popover')].find((p) => !p.hidden)!;
  ownPanel = panel;
  const q = <T extends Element>(sel: string) => panel.querySelector(sel) as unknown as T;
  return {
    button,
    choice,
    urlFor,
    panel,
    select: q<HTMLSelectElement>('.obs-popover__select'),
    url: q<HTMLInputElement>('.obs-popover__url'),
    copy: q<HTMLButtonElement>('.obs-popover__copy'),
    size: q<HTMLElement>('.obs-popover__size'),
    status: q<HTMLElement>('.obs-popover__status'),
  };
}

describe('createObsButton', () => {
  it('is an OBS button announcing its dialog', () => {
    const button = createObsButton('Ship Position');
    expect(button.textContent).toBe('OBS');
    expect(button.className).toBe('obs-btn');
    expect(button.getAttribute('aria-haspopup')).toBe('dialog');
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('toggleObsPopover', () => {
  it('opens with the selected widget URL and size, and closes on a second toggle', () => {
    const { button, choice, panel, url, size, select, urlFor } = setup();
    expect(panel.hidden).toBe(false);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(url.value).toBe('http://localhost:5173/?overlay=telemetry&s=eyJ2IjoxfQ');
    expect(urlFor).toHaveBeenCalledWith('telemetry');
    expect(size.textContent).toContain('420 × 215');
    expect(select.options).toHaveLength(2);

    toggleObsPopover(button, choice);
    expect(panel.hidden).toBe(true);
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });

  it('hides the select for a single widget', () => {
    const globe = OVERLAY_WIDGETS.filter((w) => w.id === 'globe');
    const { select } = setup({ title: 'Ship Position', widgets: globe, selected: 'globe' });
    expect((select.parentElement as HTMLElement).hidden).toBe(true);
  });

  it('rebuilds the URL when the selection changes', () => {
    const { select, url, size, urlFor } = setup();
    select.value = 'met';
    select.dispatchEvent(new Event('change'));
    expect(urlFor).toHaveBeenLastCalledWith('met');
    expect(url.value).toContain('overlay=met');
    expect(size.textContent).toContain('250 × 80');
  });

  it('closes on Escape inside it and on a press outside it', () => {
    let { panel } = setup();
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(panel.hidden).toBe(true);

    ({ panel } = setup());
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(panel.hidden).toBe(true);
  });

  it('copies the URL to the clipboard', async () => {
    const writeText = vi.fn(() => Promise.resolve());
    setClipboard(writeText);
    const { copy, url } = setup();
    copy.click();
    await vi.waitFor(() => expect(copy.textContent).toBe('Copied'));
    expect(writeText).toHaveBeenCalledWith(url.value);
  });

  it('selects the URL when the clipboard rejects', async () => {
    setClipboard(() => Promise.reject(new Error('denied')));
    const { copy, url, status } = setup();
    copy.click();
    await vi.waitFor(() => expect(status.textContent).toBe('Press Ctrl+C to copy.'));
    expect(url.selectionStart).toBe(0);
    expect(url.selectionEnd).toBe(url.value.length);
  });

  it('selects the URL when there is no clipboard API', async () => {
    setClipboard(undefined);
    const { copy, status } = setup();
    copy.click();
    await vi.waitFor(() => expect(status.textContent).toBe('Press Ctrl+C to copy.'));
  });
});
