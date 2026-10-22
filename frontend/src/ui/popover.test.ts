/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { anchorPanel, bindDismiss } from './popover';

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

/** A shown 200×100 panel and an anchor at `rect`, in jsdom's 1024×768 viewport. */
function setupAnchored(rect: { left: number; top: number; width: number; height: number }, align: 'left' | 'right') {
  expect([window.innerWidth, window.innerHeight]).toEqual([1024, 768]);
  const anchor = document.createElement('button');
  const panel = document.createElement('div');
  Object.defineProperty(panel, 'offsetWidth', { value: 200 });
  Object.defineProperty(panel, 'offsetHeight', { value: 100 });
  let box = rect;
  anchor.getBoundingClientRect = () =>
    ({ ...box, right: box.left + box.width, bottom: box.top + box.height }) as DOMRect;
  document.body.append(anchor, panel);
  const onAnchorRemoved = vi.fn();
  const anchored = anchorPanel(panel, () => anchor, { align, onAnchorRemoved });
  const moveAnchor = (next: typeof rect) => (box = next);
  const at = () => [panel.style.left, panel.style.top];
  return { anchor, panel, anchored, onAnchorRemoved, moveAnchor, at };
}

describe('anchorPanel', () => {
  it('places the panel 6 px below the anchor, lined up with its left edge', () => {
    const { anchored, at } = setupAnchored({ left: 100, top: 50, width: 30, height: 20 }, 'left');
    anchored.reposition();
    expect(at()).toEqual(['100px', '76px']);
    anchored.dispose();
  });

  it('lines the right edges up when aligned right', () => {
    const { anchored, at } = setupAnchored({ left: 500, top: 50, width: 30, height: 20 }, 'right');
    anchored.reposition();
    expect(at()).toEqual(['330px', '76px']); // 530 - 200
    anchored.dispose();
  });

  it('goes 6 px above the anchor when there is no room below', () => {
    const { anchored, at } = setupAnchored({ left: 100, top: 650, width: 30, height: 20 }, 'left');
    anchored.reposition();
    expect(at()).toEqual(['100px', '544px']); // 676 + 100 > 760, so 650 - 100 - 6
    anchored.dispose();
  });

  it('stays 8 px inside the viewport', () => {
    const right = setupAnchored({ left: 1000, top: 0, width: 20, height: 0 }, 'left');
    right.anchored.reposition();
    expect(right.at()).toEqual(['816px', '8px']); // 1024 - 200 - 8; top 6 raised to 8
    right.anchored.dispose();

    const left = setupAnchored({ left: 2, top: 50, width: 30, height: 20 }, 'right');
    left.anchored.reposition();
    expect(left.at()).toEqual(['8px', '76px']); // 32 - 200 raised to 8
    left.anchored.dispose();
  });

  it('follows the anchor on resize and on scroll anywhere while shown', () => {
    const { anchored, panel, moveAnchor, at } = setupAnchored({ left: 100, top: 50, width: 30, height: 20 }, 'left');
    anchored.reposition();
    moveAnchor({ left: 120, top: 60, width: 30, height: 20 });
    window.dispatchEvent(new Event('resize'));
    expect(at()).toEqual(['120px', '86px']);
    moveAnchor({ left: 140, top: 70, width: 30, height: 20 });
    document.body.dispatchEvent(new Event('scroll')); // scroll does not bubble: caught in capture
    expect(at()).toEqual(['140px', '96px']);

    panel.hidden = true;
    moveAnchor({ left: 160, top: 80, width: 30, height: 20 });
    window.dispatchEvent(new Event('resize'));
    expect(at()).toEqual(['140px', '96px']);
    anchored.dispose();
  });

  it('reports an anchor that left the document instead of following it', () => {
    const { anchor, anchored, onAnchorRemoved, at } = setupAnchored({ left: 100, top: 50, width: 30, height: 20 }, 'left');
    anchored.reposition();
    anchor.remove();
    window.dispatchEvent(new Event('resize'));
    expect(onAnchorRemoved).toHaveBeenCalledTimes(1);
    expect(at()).toEqual(['100px', '76px']);
    anchored.dispose();
  });

  it('stops following once disposed', () => {
    const { anchored, moveAnchor, at } = setupAnchored({ left: 100, top: 50, width: 30, height: 20 }, 'left');
    anchored.reposition();
    anchored.dispose();
    moveAnchor({ left: 120, top: 60, width: 30, height: 20 });
    window.dispatchEvent(new Event('resize'));
    document.body.dispatchEvent(new Event('scroll'));
    expect(at()).toEqual(['100px', '76px']);
  });
});
