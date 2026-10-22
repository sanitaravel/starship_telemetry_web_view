/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { bindDismiss } from './popover';

function setup(open = true) {
  const anchor = document.createElement('button');
  const panel = document.createElement('div');
  const input = document.createElement('input');
  panel.appendChild(input);
  panel.hidden = !open;
  document.body.append(anchor, panel);
  const onDismiss = vi.fn();
  const unbind = bindDismiss(panel, { onDismiss, ignore: () => [anchor] });
  return { anchor, panel, input, onDismiss, unbind };
}

afterEach(() => {
  document.body.replaceChildren();
});

describe('bindDismiss', () => {
  it('dismisses on Escape inside the panel and stops it there', () => {
    const { input, onDismiss, unbind } = setup();
    const outer = vi.fn();
    document.body.addEventListener('keydown', outer);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(onDismiss.mock.calls).toEqual([['escape']]);
    expect(outer).toHaveBeenCalledTimes(1); // only the Enter
    document.body.removeEventListener('keydown', outer);
    unbind();
  });

  it('dismisses on a press outside, not inside the panel or an ignored element', () => {
    const { anchor, input, onDismiss, unbind } = setup();
    input.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    anchor.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(onDismiss).not.toHaveBeenCalled();
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(onDismiss.mock.calls).toEqual([['outside']]);
    unbind();
  });

  it('ignores outside presses while the panel is hidden', () => {
    const { onDismiss, unbind } = setup(false);
    document.dispatchEvent(new Event('pointerdown'));
    expect(onDismiss).not.toHaveBeenCalled();
    unbind();
  });

  it('stops listening once unbound', () => {
    const { input, onDismiss, unbind } = setup();
    unbind();
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    document.dispatchEvent(new Event('pointerdown'));
    expect(onDismiss).not.toHaveBeenCalled();
  });
});
