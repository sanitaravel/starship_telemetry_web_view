/** Why a popover was dismissed: Escape inside it, or a pointer press outside it. */
export type DismissReason = 'escape' | 'outside';

export interface DismissOptions {
  /** Called on Escape (focus inside the panel) and on a pointer press outside it while open. */
  onDismiss: (reason: DismissReason) => void;
  /**
   * Elements whose presses count as inside, besides the panel (e.g. the
   * button that opens it). Read on every press, so it may change while open.
   */
  ignore?: () => readonly (Node | null | undefined)[];
}

/**
 * Wires the usual popover dismissal: Escape pressed inside `panel` (stopped
 * there, so outer Escape handlers don't also fire) and a pointerdown anywhere
 * else in the document while `panel` is shown. Returns a function that
 * removes both listeners.
 */
export function bindDismiss(panel: HTMLElement, { onDismiss, ignore }: DismissOptions): () => void {
  const onKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onDismiss('escape');
    }
  };
  const onPointerdown = (e: PointerEvent) => {
    if (panel.hidden) return;
    const target = e.target as Node;
    if (panel.contains(target) || ignore?.().some((el) => el?.contains(target))) return;
    onDismiss('outside');
  };
  panel.addEventListener('keydown', onKeydown);
  document.addEventListener('pointerdown', onPointerdown);
  return () => {
    panel.removeEventListener('keydown', onKeydown);
    document.removeEventListener('pointerdown', onPointerdown);
  };
}

export interface AnchorOptions {
  /** Which edge the panel lines up with: its left with the anchor's left, or its right with the anchor's right. */
  align: 'left' | 'right';
  /** Called on resize or scroll while shown once the anchor has left the document. */
  onAnchorRemoved: () => void;
}

export interface AnchoredPanel {
  /** Places the panel next to the current anchor, if there is one. */
  reposition: () => void;
  /** Removes the resize and scroll listeners. */
  dispose: () => void;
}

/** Space kept between a panel and the viewport's edges. */
const VIEWPORT_MARGIN = 8;
/** Space between a panel and its anchor. */
const ANCHOR_GAP = 6;

/**
 * Keeps a fixed-position `panel` next to the element `getAnchor()` returns:
 * below it, or above it when there is no room below, aligned to one of its
 * edges and kept inside the viewport. Fixed positioning would leave the panel
 * behind on scroll or resize, so while it is shown it follows the anchor on
 * both (scroll on any element, captured). `getAnchor` is read on every call,
 * so it may change while open.
 */
export function anchorPanel(
  panel: HTMLElement,
  getAnchor: () => HTMLElement | null,
  { align, onAnchorRemoved }: AnchorOptions,
): AnchoredPanel {
  const place = (anchor: HTMLElement) => {
    const margin = VIEWPORT_MARGIN;
    const rect = anchor.getBoundingClientRect();
    const { offsetWidth: width, offsetHeight: height } = panel;
    const edge = align === 'left' ? rect.left : rect.right - width;
    const left = Math.min(Math.max(margin, edge), window.innerWidth - width - margin);
    const below = rect.bottom + ANCHOR_GAP;
    const top = below + height > window.innerHeight - margin ? rect.top - height - ANCHOR_GAP : below;
    panel.style.left = `${Math.max(margin, left)}px`;
    panel.style.top = `${Math.max(margin, top)}px`;
  };
  const follow = () => {
    const anchor = getAnchor();
    if (panel.hidden || !anchor) return;
    if (anchor.isConnected) place(anchor);
    else onAnchorRemoved();
  };
  window.addEventListener('resize', follow);
  window.addEventListener('scroll', follow, true);
  return {
    reposition: () => {
      const anchor = getAnchor();
      if (anchor) place(anchor);
    },
    dispose: () => {
      window.removeEventListener('resize', follow);
      window.removeEventListener('scroll', follow, true);
    },
  };
}
