import { TelemetryWebSocket, getWebSocketUrl } from '../api/websocket';
import { createLogger } from '../core/logger';
import { StateManager } from '../state/state-manager';
import { TimelineStatusStore } from '../state/timeline-status';
import { MissionTimeline } from '../state/mission-timeline';
import type { OverlaySnapshot } from '../domain/overlay/snapshot';
import { isOverlayWidgetId, OVERLAY_WIDGETS } from '../domain/overlay/widgets';
import { OVERLAY_REGISTRY } from './overlay-registry';

const logger = createLogger('overlay');

/** What a mounted overlay runs on, for tests; null for an unknown widget. */
export interface MountedOverlay {
  stateManager: StateManager;
  socket: TelemetryWebSocket;
  timeline: MissionTimeline;
}

/** The widgets that show the mission timeline, which the others do not load. */
function usesTimeline(widget: string): boolean {
  return widget.startsWith('timeline') || widget.startsWith('graph-');
}

/**
 * Renders one widget for an OBS Browser Source: transparent page, chrome
 * off, settings from the URL snapshot only (memory-only stores, nothing in
 * localStorage), and a read-only socket that never sends a command. The
 * history comes from the backend's replay on every (re)connect.
 */
export function mountOverlay(app: HTMLElement, route: { widget: string; snapshot: OverlaySnapshot }): MountedOverlay | null {
  document.documentElement.classList.add('overlay');

  if (!isOverlayWidgetId(route.widget)) {
    logger.warn('Unknown overlay requested', { widget: route.widget });
    renderUnknown(app, route.widget);
    return null;
  }

  app.innerHTML = '<main class="overlay-root"><div id="overlay-widget"></div></main>';
  const container = app.querySelector<HTMLElement>('#overlay-widget')!;
  container.dataset.widget = route.widget;

  const stateManager = new StateManager();
  const socket = new TelemetryWebSocket({ readOnly: true });
  socket.onMessage((message) => stateManager.handleMessage(message));

  // The timeline's statuses come over the same socket; its events from the active mission's file
  const timeline = new MissionTimeline(new TimelineStatusStore());
  socket.onMessage((message) => timeline.statuses.handleMessage(message));
  if (usesTimeline(route.widget)) {
    timeline.followStatuses();
    void timeline.statuses.load();
  }

  OVERLAY_REGISTRY[route.widget](container, { stateManager, snapshot: route.snapshot, timeline });
  socket.connect(getWebSocketUrl());
  return { stateManager, socket, timeline };
}

/** "Unknown overlay" with the valid ids; no socket is opened. */
function renderUnknown(app: HTMLElement, widget: string): void {
  const box = document.createElement('div');
  box.className = 'overlay-unknown';
  const title = document.createElement('p');
  title.textContent = `Unknown overlay "${widget}". Valid overlays:`;
  const list = document.createElement('p');
  list.append(
    ...OVERLAY_WIDGETS.flatMap((w, i) => {
      const code = document.createElement('code');
      code.textContent = w.id;
      return i === 0 ? [code] : [document.createTextNode(', '), code];
    }),
  );
  box.append(title, list);
  const root = document.createElement('main');
  root.className = 'overlay-root';
  root.appendChild(box);
  app.replaceChildren(root);
}
