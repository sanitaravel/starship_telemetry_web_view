import ReconnectingWebSocket from 'reconnecting-websocket';
import type { WebSocketMessage, ControlCommand } from './types';
import { createLogger } from './logger';

const logger = createLogger('websocket');

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
  private retryCount: number = 0;

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
      this.retryCount = 0;
      this.setConnectionState('connected');
    });

    this.ws.addEventListener('close', () => {
      if (this._connectionState === 'connected' || this._connectionState === 'connecting') {
        this.retryCount++;
        logger.warn(`Reconnection attempt ${this.retryCount}`, { attempt: this.retryCount });
      }
      this.setConnectionState('disconnected');
    });

    this.ws.addEventListener('error', () => {
      if (this._connectionState === 'connected' || this._connectionState === 'connecting') {
        this.retryCount++;
        logger.warn(`Reconnection attempt ${this.retryCount}`, { attempt: this.retryCount });
      }
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
    } else {
      logger.warn(`Command send failed: action="${command.action}", state="${this._connectionState}"`, {
        action: command.action,
        connectionState: this._connectionState,
      });
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
      const previous = this._connectionState;
      this._connectionState = state;
      logger.info(`Connection state: ${previous} → ${state}`, { previous, current: state });
      for (const handler of this.connectionStateHandlers) {
        handler(state);
      }
    }
  }

  private handleMessage(data: string): void {
    // Step 1: Attempt JSON parse
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch (err: unknown) {
      const errorDesc = err instanceof Error ? err.message : 'Unknown parse error';
      const rawPreview = data.slice(0, 200);
      logger.warn(`Message parse error: ${errorDesc}`, { error: errorDesc, rawPreview });
      return;
    }

    // Step 2: Validate structure
    const message = validateMessageStructure(parsed);
    if (message) {
      logger.debug(`Message received: type="${message.type}"`, { type: message.type });
      for (const handler of this.messageHandlers) {
        handler(message);
      }
    } else {
      const typeField = (parsed && typeof parsed === 'object' && 'type' in (parsed as object))
        ? String((parsed as Record<string, unknown>).type)
        : 'unknown';
      logger.warn(`Invalid message structure: validation failed for type="${typeField}"`, {
        type: typeField,
      });
    }
  }
}

/**
 * Validate that a parsed JSON value conforms to the expected WebSocketMessage structure.
 * Returns the message if valid, or null if it fails validation.
 */
function validateMessageStructure(parsed: unknown): WebSocketMessage | null {
  if (
    typeof parsed !== 'object' ||
    parsed === null ||
    !('type' in parsed) ||
    !('payload' in parsed)
  ) {
    return null;
  }

  const { type, payload } = parsed as Record<string, unknown>;

  if (type !== 'telemetry' && type !== 'status' && type !== 'error' && type !== 'validation_result' && type !== 'frame') {
    return null;
  }

  if (typeof payload !== 'object' || payload === null) {
    return null;
  }

  return { type, payload } as WebSocketMessage;
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
