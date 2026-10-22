"""Tracking of connected WebSocket clients and broadcasting to them."""

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections for broadcasting telemetry.

    Tracks all connected clients and supports broadcasting messages
    to all of them simultaneously. Handles disconnection cleanup.
    """

    def __init__(self) -> None:
        self._active_connections: list[WebSocket] = []

    @property
    def active_connections(self) -> list[WebSocket]:
        """List of currently active WebSocket connections."""
        return self._active_connections

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self._active_connections.append(websocket)
        logger.info(
            f"WebSocket client connected. Total clients: {len(self._active_connections)}"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active list."""
        if websocket in self._active_connections:
            self._active_connections.remove(websocket)
        logger.info(
            f"WebSocket client disconnected. Total clients: {len(self._active_connections)}"
        )

    async def broadcast(self, message: str) -> None:
        """Send a text message to all connected clients concurrently.

        Clients are sent to at the same time, so one slow client does not
        delay the others. Removes any clients that fail to receive the message.
        """
        connections = list(self._active_connections)
        results = await asyncio.gather(
            *(connection.send_text(message) for connection in connections),
            return_exceptions=True,
        )
        for conn, result in zip(connections, results):
            if isinstance(result, Exception):
                self.disconnect(conn)

    async def broadcast_json(self, data: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients.

        The message is serialised once (the same way Starlette's send_json
        does) instead of once per client.
        """
        if not self._active_connections:
            return
        await self.broadcast(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
