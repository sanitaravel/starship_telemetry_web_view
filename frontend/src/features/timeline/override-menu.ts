import type { TimelineStatusValue } from '../../core/types';
import { TIMELINE_STATUSES } from '../../domain/timeline/types';
import { STATUS_LABELS } from '../../domain/timeline/view';
import { anchorPanel, bindDismiss, type AnchoredPanel } from '../../ui/popover';
import { statusIcon } from './status-circle';

/** What the menu is opened for. */
export interface OverrideMenuRequest {
  /** The status circle it opens under; focus returns to it on close. */
  anchor: HTMLElement;
  eventText: string;
  status: TimelineStatusValue;
  source: 'auto' | 'human';
  /** A status to set by hand, or null for "Back to auto". */
  onChoose(status: TimelineStatusValue | null): void;
}

/**
 * Small menu under a status circle: the six statuses and, while a person's
 * override stands, "Back to auto". Arrow keys, Home and End move between the
 * items, Enter or Space picks one, Escape closes it and returns to the circle.
 * One menu at a time (a module singleton, like the other popovers).
 */
class OverrideMenu {
  private readonly panel: HTMLElement;
  private anchor: HTMLElement | null = null;
  private anchored: AnchoredPanel;
  private unbind: (() => void) | null = null;

  constructor() {
    this.panel = document.createElement('div');
    this.panel.className = 'timeline-menu';
    this.panel.setAttribute('role', 'menu');
    this.panel.hidden = true;
    this.panel.addEventListener('keydown', (e) => this.onKeydown(e));
    document.body.appendChild(this.panel);
    this.anchored = anchorPanel(this.panel, () => this.anchor, { align: 'left', onAnchorRemoved: () => this.close(false) });
  }

  get isOpen(): boolean {
    return !this.panel.hidden;
  }

  toggle(request: OverrideMenuRequest): void {
    if (this.isOpen && this.anchor === request.anchor) {
      this.close(true);
      return;
    }
    this.close(false);
    this.open(request);
  }

  private open(request: OverrideMenuRequest): void {
    this.anchor = request.anchor;
    this.panel.setAttribute('aria-label', `Set status: ${request.eventText}`);
    const choose = (status: TimelineStatusValue | null) => {
      this.close(true);
      request.onChoose(status);
    };

    const items: HTMLButtonElement[] = TIMELINE_STATUSES.map((status) => {
      const item = this.item(STATUS_LABELS[status], () => choose(status), 'menuitemradio');
      item.dataset.status = status;
      item.setAttribute('aria-checked', String(status === request.status));
      item.prepend(statusIcon(status));
      return item;
    });
    if (request.source === 'human') {
      const back = this.item('Back to auto', () => choose(null), 'menuitem');
      back.dataset.status = 'auto';
      back.classList.add('timeline-menu__item--auto');
      items.push(back);
    }
    this.panel.replaceChildren(...items);

    this.panel.hidden = false;
    request.anchor.setAttribute('aria-expanded', 'true');
    this.anchored.reposition();
    this.unbind = bindDismiss(this.panel, {
      onDismiss: (reason) => this.close(reason === 'escape'),
      ignore: () => [this.anchor],
    });
    (items.find((i) => i.getAttribute('aria-checked') === 'true') ?? items[0]).focus();
  }

  private item(label: string, onClick: () => void, role: string): HTMLButtonElement {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'timeline-menu__item';
    item.setAttribute('role', role);
    item.tabIndex = -1;
    const text = document.createElement('span');
    text.textContent = label;
    item.appendChild(text);
    item.addEventListener('click', onClick);
    return item;
  }

  private onKeydown(e: KeyboardEvent): void {
    const items = [...this.panel.querySelectorAll<HTMLButtonElement>('.timeline-menu__item')];
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (e.key === 'ArrowDown') next = (at + 1) % items.length;
    else if (e.key === 'ArrowUp') next = (at - 1 + items.length) % items.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = items.length - 1;
    else if (e.key === 'Tab') {
      // The menu is not a tab stop of its own: leaving it closes it
      this.close(true);
      return;
    }
    if (next === null) return;
    e.preventDefault();
    items[next]?.focus();
  }

  close(returnFocus: boolean): void {
    if (!this.isOpen) return;
    this.unbind?.();
    this.unbind = null;
    this.panel.hidden = true;
    this.panel.replaceChildren();
    const anchor = this.anchor;
    this.anchor = null;
    anchor?.setAttribute('aria-expanded', 'false');
    if (returnFocus) anchor?.focus();
  }
}

let menu: OverrideMenu | null = null;

/** Opens the status menu under a circle, or closes it when it is already open there. */
export function toggleOverrideMenu(request: OverrideMenuRequest): void {
  menu ??= new OverrideMenu();
  menu.toggle(request);
}

/** Closes the menu, e.g. when its panel goes away. */
export function closeOverrideMenu(): void {
  menu?.close(false);
}
