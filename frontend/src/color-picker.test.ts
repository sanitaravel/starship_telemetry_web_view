/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { createSwatchButton, toggleColorPopover, type ColorChoice } from './color-picker';
import { LIVE_COLOR, PRESET_COLORS } from './flight-colors';

function setup(overrides: Partial<ColorChoice> = {}) {
  const swatch = createSwatchButton('test-swatch', 'Flight 13', '#e91e63');
  document.body.appendChild(swatch);
  const choice: ColorChoice = {
    title: 'Flight 13',
    color: '#e91e63',
    isCustom: false,
    onPick: vi.fn(),
    onReset: vi.fn(),
    ...overrides,
  };
  toggleColorPopover(swatch, choice);
  const panel = document.querySelector<HTMLElement>('.color-popover')!;
  const q = <T extends Element>(sel: string) => panel.querySelector(sel) as unknown as T;
  return {
    swatch,
    choice,
    panel,
    preset: (hex: string) => q<HTMLButtonElement>(`[data-color="${hex}"]`),
    input: q<HTMLInputElement>('.color-popover__input'),
    warning: q<HTMLElement>('.color-popover__warning'),
    reset: q<HTMLButtonElement>('.color-popover__reset'),
  };
}

afterEach(() => {
  document.dispatchEvent(new Event('pointerdown'));
  document.querySelectorAll('.test-swatch').forEach((el) => el.remove());
});

describe('color popover', () => {
  it('opens for the swatch with the current colour selected', () => {
    const { swatch, panel, preset, input, reset } = setup();
    expect(panel.hidden).toBe(false);
    expect(swatch.getAttribute('aria-expanded')).toBe('true');
    expect(panel.querySelector('.color-popover__title')!.textContent).toBe('Flight 13');
    expect(preset('#e91e63').getAttribute('aria-pressed')).toBe('true');
    expect(input.value).toBe('#e91e63');
    expect(reset.disabled).toBe(true);
  });

  it('applies a preset and keeps the popover open', () => {
    const { choice, panel, preset, reset } = setup();
    preset(PRESET_COLORS[0]).click();
    expect(choice.onPick).toHaveBeenCalledWith(PRESET_COLORS[0]);
    expect(preset(PRESET_COLORS[0]).getAttribute('aria-pressed')).toBe('true');
    expect(reset.disabled).toBe(false);
    expect(panel.hidden).toBe(false);
  });

  it('applies a custom colour and warns when it is close to the live orange', () => {
    const { choice, input, warning } = setup();
    expect(warning.hidden).toBe(true);
    input.value = LIVE_COLOR.toLowerCase();
    input.dispatchEvent(new Event('input'));
    expect(choice.onPick).toHaveBeenCalledWith(LIVE_COLOR.toLowerCase());
    expect(warning.hidden).toBe(false);
  });

  it('resets a custom colour and closes', () => {
    const { choice, panel, reset, swatch } = setup({ isCustom: true });
    reset.click();
    expect(choice.onReset).toHaveBeenCalled();
    expect(panel.hidden).toBe(true);
    expect(swatch.getAttribute('aria-expanded')).toBe('false');
  });

  it('closes on Escape, on a click outside, and on a second swatch click', () => {
    let { swatch, panel, choice } = setup();
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(panel.hidden).toBe(true);
    expect(document.activeElement).toBe(swatch);

    toggleColorPopover(swatch, choice);
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(panel.hidden).toBe(true);

    ({ swatch, panel, choice } = setup());
    toggleColorPopover(swatch, choice);
    expect(panel.hidden).toBe(true);
  });
});
