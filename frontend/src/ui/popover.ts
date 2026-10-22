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
