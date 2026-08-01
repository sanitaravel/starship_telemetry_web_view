import ReconnectingWebSocket from 'reconnecting-websocket';
import type { WebSocketMessage, ControlCommand } from './types';

export type ConnectionState = 'connected' | 'disconnected' | 'connecting';

export type MessageHandler = (message: WebSocketMessage) => void;
export type ConnectionStateHandler = (state: ConnectionState) => void;

/**
 * WebSocket client module with auto-reconnection.
 * Connects to the backend at /ws/telemetry and dispatches incoming messages
 * to registered handlers based on message type.
 */
export class TelemetryWebSocket {
  private ws: ReconnectingWebSocket | null = null;
  private messageHandlers: MessageHandler[] = [];
  private connectionStateHandlers: ConnectionStateHandler[] = [];
  private _connectionState: ConnectionState = 'disconnected';

  get connectionState(): ConnectionState {
    return this._connectionState;
  }

  /**
   * Connect to the WebSocket server.
   * @param url - WebSocket URL (e.g., ws://localhost:8000/ws/telemetry)
   */
  connect(url: string): void {
    if (this.ws) {
      this.ws.close();
    }

    this.setConnectionState('connecting');

    this.ws = new ReconnectingWebSocket(url, [], {
      maxReconnectionDelay: 10000,
      minReconnectionDelay: 1000,
      reconnectionDelayGrowFactor: 1.5,
      maxRetries: Infinity,
    });

    this.ws.addEventListener('open', () => {
      this.setConnectionState('connected');
    });

    this.ws.addEventListener('close', () => {
      this.setConnectionState('disconnected');
    });

    this.ws.addEventListener('error', () => {
      this.setConnectionState('disconnected');
    });

    this.ws.addEventListener('message', (event: MessageEvent) => {
      this.handleMessage(event.data);
    });
  }

  /**
   * Disconnect from the WebSocket server.
   */
  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.setConnectionState('disconnected');
  }

  /**
   * Send a control command to the backend.
   */
  sendCommand(command: ControlCommand): void {
    if (this.ws && this._connectionState === 'connected') {
      this.ws.send(JSON.stringify(command));
    }
  }

  /**
   * Register a handler for incoming WebSocket messages.
   */
  onMessage(handler: MessageHandler): () => void {
    this.messageHandlers.push(handler);
    return () => {
      this.messageHandlers = this.messageHandlers.filter((h) => h !== handler);
    };
  }

  /**
   * Register a handler for connection state changes.
   */
  onConnectionStateChange(handler: ConnectionStateHandler): () => void {
    this.connectionStateHandlers.push(handler);
    return () => {
      this.connectionStateHandlers = this.connectionStateHandlers.filter((h) => h !== handler);
    };
  }

  private setConnectionState(state: ConnectionState): void {
    if (this._connectionState !== state) {
      this._connectionState = state;
      for (const handler of this.connectionStateHandlers) {
        handler(state);
      }
    }
  }

  private handleMessage(data: string): void {
    try {
      const message = parseWebSocketMessage(data);
      if (message) {
        for (const handler of this.messageHandlers) {
          handler(message);
        }
      }
    } catch {
      // Silently ignore unparseable messages
    }
  }
}

/**
 * Parse a raw JSON string into a WebSocketMessage.
 * Returns null if the message is invalid.
 */
export function parseWebSocketMessage(data: string): WebSocketMessage | null {
  const parsed = JSON.parse(data);

  if (
    typeof parsed !== 'object' ||
    parsed === null ||
    !('type' in parsed) ||
    !('payload' in parsed)
  ) {
    return null;
  }

  const { type, payload } = parsed;

  if (type !== 'telemetry' && type !== 'status' && type !== 'error' && type !== 'validation_result' && type !== 'frame') {
    return null;
  }

  if (typeof payload !== 'object' || payload === null) {
    return null;
  }

  return { type, payload } as WebSocketMessage;
}

/**
 * Serialize a ControlCommand to a JSON string.
 */
export function serializeCommand(command: ControlCommand): string {
  return JSON.stringify(command);
}

/**
 * Create a default TelemetryWebSocket instance connecting to the standard endpoint.
 */
export function createTelemetryWebSocket(): TelemetryWebSocket {
  return new TelemetryWebSocket();
}

/**
 * Build the WebSocket URL from the current page location.
 */
export function getWebSocketUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws/telemetry`;
}
