import { describe, it, expect, vi, afterEach } from 'vitest';
import { fetchTimelineStatus, overrideTimelineStatus, setActiveTimelineMission } from './timeline';
import { parseWebSocketMessage } from './websocket';

const snapshot = { mission_id: 'ift-14', run_id: 'r', revision: 2, met: null, clock_source: null, pending: 0, events: [] };

describe('timeline status API', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('GETs the snapshot', async () => {
    const fetchFn = vi.fn(async () => ({ ok: true, json: async () => snapshot }));
    vi.stubGlobal('fetch', fetchFn);
    await expect(fetchTimelineStatus()).resolves.toEqual(snapshot);
    expect(fetchFn).toHaveBeenCalledWith('/api/timeline/status');
  });

  it('treats a body without events as no snapshot', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ detail: 'x' }) })));
    await expect(fetchTimelineStatus()).resolves.toBeNull();
  });

  it('POSTs an override, and null for back to auto', async () => {
    const fetchFn = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => snapshot }));
    vi.stubGlobal('fetch', fetchFn);
    await overrideTimelineStatus('sh-meco', 'partial');
    await overrideTimelineStatus('sh meco/2', null);
    expect(fetchFn.mock.calls[0][0]).toBe('/api/timeline/sh-meco/status');
    expect(fetchFn.mock.calls[0][1]).toMatchObject({ method: 'POST', body: '{"status":"partial"}' });
    expect(fetchFn.mock.calls[1][0]).toBe('/api/timeline/sh%20meco%2F2/status');
    expect(fetchFn.mock.calls[1][1]?.body).toBe('{"status":null}');
  });

  it('rejects an override the backend refuses', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) })));
    await expect(overrideTimelineStatus('nope', 'success')).rejects.toThrow('404');
  });

  it('PUTs the active mission', async () => {
    const fetchFn = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => snapshot }));
    vi.stubGlobal('fetch', fetchFn);
    await setActiveTimelineMission('ift-14');
    expect(fetchFn.mock.calls[0][0]).toBe('/api/timeline/active');
    expect(fetchFn.mock.calls[0][1]).toMatchObject({ method: 'PUT', body: '{"mission_id":"ift-14"}' });
  });
});

describe('timeline_status messages', () => {
  it('are accepted with events and a revision', () => {
    const message = parseWebSocketMessage(JSON.stringify({ type: 'timeline_status', payload: snapshot }));
    expect(message?.type).toBe('timeline_status');
  });

  it('are rejected without them', () => {
    expect(parseWebSocketMessage(JSON.stringify({ type: 'timeline_status', payload: { events: 'x', revision: 1 } }))).toBeNull();
    expect(parseWebSocketMessage(JSON.stringify({ type: 'timeline_status', payload: { events: [] } }))).toBeNull();
  });
});
