/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { BackendVersion } from './backend-version';
import type { AppState } from './state';

// The page's own version, as injected at build time
vi.mock('./core/app-version', () => ({ appVersion: () => '0.1.0.64' }));

const flush = () => new Promise((r) => setTimeout(r, 0));

function setup(fetchVersion: string | null) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: fetchVersion !== null,
      json: async () => ({ status: 'stopped', version: fetchVersion }),
    })),
  );
  let handler: (state: AppState) => void = () => {};
  const stateManager = { subscribe: vi.fn((h) => (handler = h)) };
  const element = document.createElement('span');
  new BackendVersion(element, stateManager as never);
  const pushStatus = (version: string) =>
    handler({ pipelineStatus: { status: 'running', version } } as unknown as AppState);
  return { element, pushStatus };
}

describe('BackendVersion', () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it('shows the backend version from /api/status', async () => {
    const { element } = setup('0.1.0.64');
    await flush();
    expect(element.textContent).toBe('backend v0.1.0.64');
    expect(element.classList.contains('dashboard-header__version--mismatch')).toBe(false);
  });

  it('flags a backend running a different version', async () => {
    const { element } = setup('0.1.0');
    await flush();
    expect(element.textContent).toBe('backend v0.1.0');
    expect(element.classList.contains('dashboard-header__version--mismatch')).toBe(true);
    expect(element.title).toMatch(/old one may still be running/);
  });

  it('updates from status messages', async () => {
    const { element, pushStatus } = setup(null);
    await flush();
    expect(element.textContent).toBe('backend unknown');
    pushStatus('0.1.0.64');
    expect(element.textContent).toBe('backend v0.1.0.64');
  });
});
