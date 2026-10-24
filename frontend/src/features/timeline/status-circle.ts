import type { TimelineStatusValue } from '../../core/types';
import { STATUS_COLORS, STATUS_LABELS } from '../../domain/timeline/view';
import { svgEl } from '../../ui/svg';

const SIZE = 18;
const CENTER = SIZE / 2;
const RADIUS = 6.5;

/**
 * The drawing for a status, 18 px square and sized by CSS. Each status has
 * its own shape as well as its colour (hollow ring, pulsing ring, ✓, half
 * filled, ✕, dashed ring with a slash), so none relies on colour alone.
 */
export function statusIcon(status: TimelineStatusValue): SVGSVGElement {
  const color = STATUS_COLORS[status];
  const svg = svgEl('svg', {
    viewBox: `0 0 ${SIZE} ${SIZE}`,
    class: `timeline-circle__icon timeline-circle__icon--${status}`,
    'aria-hidden': 'true',
    focusable: 'false',
  });
  const ring = (extra: Record<string, string> = {}) =>
    svgEl('circle', {
      cx: String(CENTER),
      cy: String(CENTER),
      r: String(RADIUS),
      fill: 'none',
      stroke: color,
      'stroke-width': '2',
      ...extra,
    });
  const mark = (d: string) =>
    svgEl('path', { d, fill: 'none', stroke: '#fff', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });

  switch (status) {
    case 'not_yet':
      svg.append(ring());
      break;
    case 'pending':
      // A dot inside the ring, pulsing in CSS, tells it from the hollow one
      svg.append(ring(), svgEl('circle', { cx: String(CENTER), cy: String(CENTER), r: '2.5', fill: color }));
      break;
    case 'success':
      svg.append(
        svgEl('circle', { cx: String(CENTER), cy: String(CENTER), r: String(RADIUS + 1), fill: color }),
        mark('M5.5 9.5 L8 12 L12.5 6.5'),
      );
      break;
    case 'partial':
      svg.append(
        ring(),
        // The left half filled
        svgEl('path', { d: `M${CENTER} ${CENTER - RADIUS} A${RADIUS} ${RADIUS} 0 0 0 ${CENTER} ${CENTER + RADIUS} Z`, fill: color }),
      );
      break;
    case 'failure':
      svg.append(
        svgEl('circle', { cx: String(CENTER), cy: String(CENTER), r: String(RADIUS + 1), fill: color }),
        mark('M6 6 L12 12 M12 6 L6 12'),
      );
      break;
    case 'skipped':
      svg.append(
        ring({ 'stroke-dasharray': '2.5 2.5' }),
        svgEl('path', { d: 'M4.5 13.5 L13.5 4.5', stroke: color, 'stroke-width': '2', 'stroke-linecap': 'round' }),
      );
      break;
  }
  return svg;
}

/**
 * Puts a status into a circle element (a button in the dashboard, a span in
 * an overlay): the drawing, a small "H" badge while a person's override
 * stands, and the tooltip and accessible name.
 */
export function fillStatusCircle(
  element: HTMLElement,
  status: TimelineStatusValue,
  source: 'auto' | 'human',
  tooltip: string,
  eventText: string,
): void {
  element.className = element.className
    .split(/\s+/)
    .filter((c) => c && !c.startsWith('timeline-circle--'))
    .concat(`timeline-circle--${status}`)
    .join(' ');
  if (!element.classList.contains('timeline-circle')) element.classList.add('timeline-circle');
  element.dataset.status = status;
  element.dataset.source = source;
  element.title = tooltip;
  element.setAttribute('aria-label', `${eventText}: ${STATUS_LABELS[status]}${source === 'human' ? ', set by hand' : ''}`);

  const children: Node[] = [statusIcon(status)];
  if (source === 'human') {
    const badge = document.createElement('span');
    badge.className = 'timeline-circle__badge';
    badge.setAttribute('aria-hidden', 'true');
    badge.textContent = 'H';
    children.push(badge);
  }
  element.replaceChildren(...children);
}
