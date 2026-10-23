"""Tracking of connected WebSocket clients and broadcasting to them.

A client connecting with a replay source is first sent the replay (the Run so
far) and only then goes live. While it is replaying, broadcasts to it are
queued and flushed in order afterwards, so it sees every telemetry record
exactly once. Only ``connect()`` writes to a replaying client: a Starlette
WebSocket must never be sent to from two tasks at once.
"""

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable, Iterable

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Messages queued for one replaying client before it is closed as too slow
MAX_PENDING_MESSAGES = 2_000

# WebSocket close code "Try Again Later", sent to a client that fell too far behind
_CLOSE_TRY_AGAIN_LATER = 1013

# Called synchronously by connect(); must take its snapshot before returning
# (the texts themselves may be produced lazily)
ReplaySource = Callable[[], Iterable[str]]


class ConnectionManager:
    """Manages active WebSocket connections for broadcasting telemetry.

    Tracks all connected clients and supports broadcasting messages
    to all of them simultaneously. Handles disconnection cleanup.
    """

    def __init__(self) -> None:
        self._active_connections: list[WebSocket] = []
        # Clients still being sent their replay, with the broadcasts queued for them
        self._pending: dict[WebSocket, deque[str]] = {}
        # Replaying clients whose queue overflowed; connect() closes them
        self._overflowed: set[WebSocket] = set()

    @property
    def active_connections(self) -> list[WebSocket]:
        """List of currently active WebSocket connections."""
        return self._active_connections

    def is_replaying(self, websocket: WebSocket) -> bool:
        """Whether the client is still being sent its replay."""
        return websocket in self._pending

    async def connect(self, websocket: WebSocket, replay: ReplaySource | None = None) -> None:
        """Accept and register a new WebSocket connection.

        With a replay source, the replay is sent first and the broadcasts made
        meanwhile are flushed after it, in order, before the client goes live.

        Raises:
            WebSocketDisconnect: The client fell more than MAX_PENDING_MESSAGES
                behind during its replay and was closed.
            Exception: Whatever send_text raises if the client goes away.
        """
        await websocket.accept()
        if replay is None:
            self._active_connections.append(websocket)
            self._log_connected()
            return

        # --- NO AWAIT from here to the end of this block. ---
        # Taking the snapshot and registering the client as replaying must be
        # one synchronous step: a record broadcast before it is in the snapshot
        # and not routed here, one broadcast after it is queued here and not in
        # the snapshot, so each record reaches this client exactly once.
        messages = replay()
        self._active_connections.append(websocket)
        self._pending[websocket] = deque()
        # --- end of the no-await block ---
        self._log_connected()

        for text in messages:
            if not await self._still_replaying(websocket):
                return
            await websocket.send_text(text)

        # Flush what was broadcast during the replay. The emptiness check and
        # the switch to live (del) are one synchronous step, so nothing can be
        # queued after the last flushed message and then never be sent.
        while True:
            if not await self._still_replaying(websocket):
                return
            queue = self._pending[websocket]
            if not queue:
                del self._pending[websocket]
                return
            await websocket.send_text(queue.popleft())

    async def _still_replaying(self, websocket: WebSocket) -> bool:
        """Whether connect() should keep sending to a replaying client.

        Closes a client whose queue overflowed (here, so connect() stays the
        only task writing to it) and ends the session with WebSocketDisconnect.
        Returns False if the client was disconnected meanwhile.
        """
        if websocket in self._overflowed:
            self._overflowed.discard(websocket)
            await websocket.close(code=_CLOSE_TRY_AGAIN_LATER)
            raise WebSocketDisconnect(code=_CLOSE_TRY_AGAIN_LATER)
        return websocket in self._pending

    def _log_connected(self) -> None:
        logger.info(
            f"WebSocket client connected. Total clients: {len(self._active_connections)}"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection (and any replay queue) from the manager."""
        if websocket in self._active_connections:
            self._active_connections.remove(websocket)
        self._pending.pop(websocket, None)
        self._overflowed.discard(websocket)
        logger.info(
            f"WebSocket client disconnected. Total clients: {len(self._active_connections)}"
        )

    async def broadcast(self, message: str, droppable: bool = False) -> None:
        """Send a text message to all connected clients concurrently.

        Clients are sent to at the same time, so one slow client does not
        delay the others. Removes any clients that fail to receive the message.
        Clients still replaying get the message queued instead, or not at all
        if it is droppable (a frame preview).

        Args:
            message: The text to send.
            droppable: Skip replaying clients instead of queueing it for them.
        """
        # --- NO AWAIT before the gather. ---
        # Which clients get the message live and which have it queued is
        # decided in the same synchronous step as the caller's history append
        # (see Broadcaster._worker) and against the same client list.
        live: list[WebSocket] = []
        for connection in list(self._active_connections):
            queue = self._pending.get(connection)
            if queue is None:
                live.append(connection)
            elif not droppable:
                queue.append(message)
                if len(queue) > MAX_PENDING_MESSAGES:
                    self._drop_slow_client(connection)
        if not live:
            return

        results = await asyncio.gather(
            *(connection.send_text(message) for connection in live),
            return_exceptions=True,
        )
        for conn, result in zip(live, results):
            if isinstance(result, Exception):
                self.disconnect(conn)

    def _drop_slow_client(self, websocket: WebSocket) -> None:
        """Unregister a replaying client whose queue overflowed; connect() closes it."""
        logger.warning(
            "WebSocket client fell more than %d messages behind during its replay, "
            "closing it.",
            MAX_PENDING_MESSAGES,
        )
        self.disconnect(websocket)
        self._overflowed.add(websocket)

    async def broadcast_json(self, data: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients.

        The message is serialised once (the same way Starlette's send_json
        does) instead of once per client. Frame previews are not queued for
        clients that are still replaying.
        """
        if not self._active_connections:
            return
        await self.broadcast(
            json.dumps(data, separators=(",", ":"), ensure_ascii=False),
            droppable=data.get("type") == "frame",
        )
