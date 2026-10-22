import { describe, it, expect, vi, afterEach } from 'vitest';
import { fetchPipelineStatus } from './status';

describe('fetchPipelineStatus', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('requests /api/status and returns the parsed body', async () => {
    const fetchFn = vi.fn(async () => ({ ok: true, json: async () => ({ status: 'stopped', version: '1.2.3' }) }));
    vi.stubGlobal('fetch', fetchFn);
    await expect(fetchPipelineStatus()).resolves.toEqual({ status: 'stopped', version: '1.2.3' });
    expect(fetchFn).toHaveBeenCalledWith('/api/status');
  });

  it('returns undefined on an HTTP error without reading the body', async () => {
    const json = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json })));
    await expect(fetchPipelineStatus()).resolves.toBeUndefined();
    expect(json).not.toHaveBeenCalled();
  });

  it('passes a JSON null body through unchanged (distinct from undefined)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => null })));
    await expect(fetchPipelineStatus()).resolves.toBeNull();
  });

  it('throws network and JSON errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    await expect(fetchPipelineStatus()).rejects.toThrow('offline');
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => { throw new SyntaxError('bad'); } })));
    await expect(fetchPipelineStatus()).rejects.toThrow('bad');
  });
});
