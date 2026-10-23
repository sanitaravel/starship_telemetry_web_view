import { TelemetryWebSocket, getWebSocketUrl } from '../api/websocket';
import { createLogger } from '../core/logger';
import { StateManager } from '../state/state-manager';
import type { OverlaySnapshot } from '../domain/overlay/snapshot';
import { isOverlayWidgetId, OVERLAY_WIDGETS } from '../domain/overlay/widgets';
import { OVERLAY_REGISTRY } from './overlay-registry';

const logger = createLogger('overlay');

/** What a mounted overlay runs on, for tests; null for an unknown widget. */
export interface MountedOverlay {
  stateManager: StateManager;
  socket: TelemetryWebSocket;
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

  OVERLAY_REGISTRY[route.widget](container, { stateManager, snapshot: route.snapshot });
  socket.connect(getWebSocketUrl());
  return { stateManager, socket };
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
