/**
 * @vitest-environment jsdom
 */
import { describe, it, expect } from 'vitest';
import { TIMELINE_STATUSES } from '../../domain/timeline/types';
import { STATUS_COLORS, STATUS_LABELS } from '../../domain/timeline/view';
import { fillStatusCircle, statusIcon } from './status-circle';

/** The icon's drawing as shape names, to compare statuses by shape rather than colour. */
function shape(status: (typeof TIMELINE_STATUSES)[number]): string {
  return [...statusIcon(status).children]
    .map((child) => `${child.tagName}:${child.getAttribute('fill')}:${child.getAttribute('stroke-dasharray') ?? ''}:${child.getAttribute('d') ?? ''}`)
    .join('|');
}

describe('statusIcon', () => {
  it('draws every status with its own shape, not only its own colour', () => {
    const shapes = TIMELINE_STATUSES.map((s) => shape(s).replaceAll(STATUS_COLORS[s], 'C'));
    expect(new Set(shapes).size).toBe(TIMELINE_STATUSES.length);
  });

  it('hollow grey ring for not yet, dashed ring with a slash for skipped', () => {
    const notYet = statusIcon('not_yet');
    expect(notYet.querySelectorAll('circle')).toHaveLength(1);
    expect(notYet.querySelector('circle')!.getAttribute('fill')).toBe('none');
    const skipped = statusIcon('skipped');
    expect(skipped.querySelector('circle')!.getAttribute('stroke-dasharray')).toBeTruthy();
    expect(skipped.querySelector('path')).not.toBeNull();
  });

  it('fills success and failure with their colour and a mark inside', () => {
    for (const status of ['success', 'failure'] as const) {
      const icon = statusIcon(status);
      expect(icon.querySelector('circle')!.getAttribute('fill')).toBe(STATUS_COLORS[status]);
      expect(icon.querySelector('path')).not.toBeNull();
    }
  });

  it('half fills partial and puts a dot in the ring of pending', () => {
    const partial = statusIcon('partial');
    expect(partial.querySelector('path')!.getAttribute('fill')).toBe(STATUS_COLORS.partial);
    expect(partial.querySelector('circle')!.getAttribute('fill')).toBe('none');
    expect(statusIcon('pending').querySelectorAll('circle')).toHaveLength(2);
  });

  it('is hidden from assistive technology, the circle carrying the name', () => {
    expect(statusIcon('success').getAttribute('aria-hidden')).toBe('true');
  });
});

describe('fillStatusCircle', () => {
  it('names the status in the label and tooltip, and marks a human decision with a badge', () => {
    const el = document.createElement('span');
    fillStatusCircle(el, 'failure', 'human', 'tip', 'Landing burn');
    expect(el.dataset.status).toBe('failure');
    expect(el.classList.contains('timeline-circle--failure')).toBe(true);
    expect(el.title).toBe('tip');
    expect(el.getAttribute('aria-label')).toBe(`Landing burn: ${STATUS_LABELS.failure}, set by hand`);
    expect(el.querySelector('.timeline-circle__badge')?.textContent).toBe('H');
  });

  it('has no badge for an automatic status and replaces the previous status', () => {
    const el = document.createElement('button');
    fillStatusCircle(el, 'pending', 'human', 't', 'x');
    fillStatusCircle(el, 'success', 'auto', 't', 'x');
    expect(el.querySelector('.timeline-circle__badge')).toBeNull();
    expect(el.querySelectorAll('svg')).toHaveLength(1);
    expect(el.className.split(' ').filter((c) => c.startsWith('timeline-circle--'))).toEqual(['timeline-circle--success']);
    expect(el.getAttribute('aria-label')).toBe(`x: ${STATUS_LABELS.success}`);
  });
});
