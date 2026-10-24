"""Ordered broadcast queue, drained by a single task.

Sending to clients never holds up frame processing or its concurrency slot:
results are queued in order and a worker task sends them one at a time.
"""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from src.pipeline.history import TelemetryHistory

logger = logging.getLogger(__name__)

# Previews are dropped (telemetry never is) once this many messages are
# waiting to be broadcast, so a slow client cannot build up a frame backlog.
_MAX_QUEUED_BROADCASTS = 8


class Broadcaster:
    """Owns the broadcast queue and the worker task that drains it."""

    def __init__(
        self,
        broadcast: Callable[[dict[str, Any]], Awaitable[None]],
        history: TelemetryHistory | None = None,
        after_telemetry: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize the broadcaster.

        Args:
            broadcast: Async callback that sends a JSON message to all
                connected WebSocket clients.
            history: Store that each telemetry record is appended to as it
                is sent, for replay to clients that connect later.
            after_telemetry: Awaited with each telemetry payload right after
                it was broadcast (the timeline statuses follow the records).
        """
        self._broadcast = broadcast
        self._history = history
        self._after_telemetry = after_telemetry
        self.queue: asyncio.Queue[dict[str, Any]] | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Create the queue and start the worker task."""
        self.queue = asyncio.Queue()
        self._task = asyncio.create_task(self._worker())

    def stop(self) -> None:
        """Cancel the worker; anything still queued belongs to the stopped run."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.queue = None

    def enqueue(self, message: dict[str, Any], droppable: bool = False) -> None:
        """Queue a message for the worker task, preserving order.

        Args:
            message: The JSON-serialisable message to send to every client.
            droppable: Drop the message instead of queueing it when the queue
                already holds _MAX_QUEUED_BROADCASTS messages.
        """
        if self.queue is None:
            return
        if droppable and self.queue.qsize() >= _MAX_QUEUED_BROADCASTS:
            logger.debug("Broadcast queue full, dropping frame preview.")
            return
        self.queue.put_nowait(message)

    async def _worker(self) -> None:
        """Send queued messages to clients one at a time, in queue order."""
        assert self.queue is not None
        queue = self.queue
        while True:
            message = await queue.get()
            start = time.monotonic()
            try:
                # NO AWAIT between the append and the call into broadcast:
                # the broadcast callback routes the message (live or queued
                # per client) before its first await, so appending and routing
                # are one synchronous step and a client connecting at any
                # point gets each record exactly once (replay or live).
                if self._history is not None and message.get("type") == "telemetry":
                    self._history.append(message["payload"])
                await self._broadcast(message)
                if self._after_telemetry is not None and message.get("type") == "telemetry":
                    await self._after_telemetry(message["payload"])
            except Exception as e:
                logger.warning("Failed to broadcast %s message: %s", message.get("type"), e)
            logger.debug(
                "Broadcast completed",
                extra={"duration_ms": int((time.monotonic() - start) * 1000)},
            )
