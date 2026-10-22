/**
 * @vitest-environment jsdom
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { PipelineStatus } from '../../core/types';
import {
  renderValidationStatus,
  renderActionButton,
  renderStatusBadge,
  renderFps,
} from './status';

const statusOf = (status: PipelineStatus['status']): PipelineStatus => ({
  status,
  gpu: { available: false, device_name: null },
  skip_frames: 30,
  current_sequence: 0,
});

const makeButton = (): HTMLButtonElement => document.createElement('button');
const makeSpan = (): HTMLElement => document.createElement('span');

describe('renderValidationStatus', () => {
  it('shows the text and class for each validation state', () => {
    const indicator = makeSpan();
    renderValidationStatus(indicator, 'checking');
    expect(indicator.textContent).toBe('Checking...');
    expect(indicator.classList.contains('pipeline-controls__validation-status--checking')).toBe(true);

    renderValidationStatus(indicator, 'active');
    expect(indicator.textContent).toBe('Stream active');
    expect(indicator.classList.contains('pipeline-controls__validation-status--checking')).toBe(false);
    expect(indicator.classList.contains('pipeline-controls__validation-status--active')).toBe(true);

    renderValidationStatus(indicator, 'unreachable');
    expect(indicator.textContent).toBe('Stream unreachable');
    expect(indicator.classList.contains('pipeline-controls__validation-status--active')).toBe(false);
    expect(indicator.classList.contains('pipeline-controls__validation-status--unreachable')).toBe(true);
  });

  it('clears the text and every status class for idle', () => {
    const indicator = makeSpan();
    renderValidationStatus(indicator, 'active');
    renderValidationStatus(indicator, 'idle');
    expect(indicator.textContent).toBe('');
    expect(indicator.className).toBe('');
  });
});

describe('renderActionButton', () => {
  it('shows Stop while running, whatever the validation state', () => {
    const btn = makeButton();
    renderActionButton(btn, statusOf('running'), 'idle');
    expect(btn.textContent).toBe('Stop');
    expect(btn.style.display).toBe('');
    expect(btn.classList.contains('pipeline-controls__action-btn--stop')).toBe(true);
  });

  it('shows Start only when the stream is validated and not running', () => {
    const btn = makeButton();
    renderActionButton(btn, statusOf('stopped'), 'active');
    expect(btn.textContent).toBe('Start');
    expect(btn.classList.contains('pipeline-controls__action-btn--start')).toBe(true);
  });

  it('hides the button when not running and not validated', () => {
    const btn = makeButton();
    renderActionButton(btn, null, 'checking');
    expect(btn.style.display).toBe('none');
  });

  it('hides the button for any non-running status unless the stream is validated', () => {
    fc.assert(
      fc.property(
        fc.constantFrom<PipelineStatus['status']>('stopped', 'disconnected', 'reconnecting'),
        fc.constantFrom<'idle' | 'checking' | 'unreachable'>('idle', 'checking', 'unreachable'),
        (status, validation) => {
          const btn = makeButton();
          renderActionButton(btn, statusOf(status), validation);
          expect(btn.style.display).toBe('none');
        }
      )
    );
  });
});

describe('renderStatusBadge', () => {
  it('capitalises the status and sets its modifier class', () => {
    const badge = makeSpan();
    renderStatusBadge(badge, statusOf('reconnecting'));
    expect(badge.textContent).toBe('Reconnecting');
    expect(badge.classList.contains('pipeline-controls__status-badge--reconnecting')).toBe(true);
  });

  it('clears the badge when there is no status yet', () => {
    const badge = makeSpan();
    renderStatusBadge(badge, statusOf('running'));
    renderStatusBadge(badge, null);
    expect(badge.textContent).toBe('');
    expect(badge.classList.contains('pipeline-controls__status-badge--running')).toBe(false);
  });
});

describe('renderFps', () => {
  it('formats the FPS to one decimal while running', () => {
    const indicator = makeSpan();
    renderFps(indicator, statusOf('running'), 12.345);
    expect(indicator.textContent).toBe('12.3 FPS');
  });

  it('is empty when not running or when the FPS is not positive', () => {
    const indicator = makeSpan();
    renderFps(indicator, statusOf('stopped'), 10);
    expect(indicator.textContent).toBe('');
    renderFps(indicator, statusOf('running'), 0);
    expect(indicator.textContent).toBe('');
    renderFps(indicator, null, 10);
    expect(indicator.textContent).toBe('');
  });

  it('formats any positive FPS as a number with one decimal', () => {
    fc.assert(
      fc.property(fc.double({ min: 0.01, max: 1000, noNaN: true }), (fps) => {
        const indicator = makeSpan();
        renderFps(indicator, statusOf('running'), fps);
        expect(indicator.textContent).toBe(`${fps.toFixed(1)} FPS`);
      })
    );
  });
});
