"""The /ws/telemetry endpoint: live telemetry broadcast and control commands."""

import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.api.runtime import manager, orchestrator
from src.logging_context import correlation_id_var

logger = logging.getLogger(__name__)

router = APIRouter()

# UUID v4 lowercase hyphenated format validation pattern
_UUID4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _is_valid_uuid4(value: str) -> bool:
    """Check if a string is a valid UUID v4 in lowercase hyphenated format."""
    return bool(_UUID4_PATTERN.match(value))


# --- WebSocket control commands ---


async def _broadcast_status() -> None:
    """Broadcast the current pipeline status to all connected clients."""
    await manager.broadcast_json({
        "type": "status",
        "payload": orchestrator.build_status_payload(),
    })


async def _handle_start(websocket: WebSocket, data: dict[str, Any]) -> None:
    """Start the pipeline on the requested source URL."""
    source_url = data.get("source_url", "")
    skip_frames = data.get("skip_frames", 30)

    await orchestrator.start(source_url, skip_frames)


async def _handle_stop(websocket: WebSocket, data: dict[str, Any]) -> None:
    """Stop the pipeline and broadcast the resulting status."""
    await orchestrator.stop()

    # Broadcast status update after stopping
    await _broadcast_status()


async def _handle_validate_url(websocket: WebSocket, data: dict[str, Any]) -> None:
    """Validate a source URL and reply to the requesting client only."""
    url = data.get("url", "")
    error = await orchestrator.frame_extractor.validate(url)
    result = {
        "type": "validation_result",
        "payload": {
            "valid": error is None,
            "message": "" if error is None else error.message,
            "url": url,
        },
    }
    await websocket.send_json(result)


async def _handle_set_interval(websocket: WebSocket, data: dict[str, Any]) -> None:
    """Update the frame skip count and broadcast the resulting status."""
    skip_frames = data.get("skip_frames", 30)
    orchestrator.set_skip_frames(skip_frames)

    await _broadcast_status()


_ACTION_HANDLERS: dict[
    str, Callable[[WebSocket, dict[str, Any]], Awaitable[None]]
] = {
    "start": _handle_start,
    "stop": _handle_stop,
    "validate_url": _handle_validate_url,
    "set_interval": _handle_set_interval,
}


@router.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time telemetry streaming.

    Accepts WebSocket connections from Dashboard clients.
    Pushes TelemetryRecord JSON on each new frame via broadcast.
    Accepts Start/Stop/ValidateUrl control commands from clients.

    Control command format:
    - {"action": "start", "source_url": "...", "interval_ms": 1000}
    - {"action": "stop"}
    - {"action": "validate_url", "url": "..."}
    """
    # Generate correlation ID for this session and record connect time
    session_correlation_id = str(uuid.uuid4())
    correlation_id_var.set(session_correlation_id)
    connect_time = time.monotonic()

    logger.info(
        "WebSocket session started",
        extra={"correlation_id": session_correlation_id},
    )

    await manager.connect(websocket)
    session_error: Exception | None = None
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            # Check for client-provided correlation_id in the payload
            client_cid = data.get("correlation_id")
            if client_cid is not None:
                if isinstance(client_cid, str) and _is_valid_uuid4(client_cid):
                    # Valid client-provided UUID v4 — adopt it
                    correlation_id_var.set(client_cid)
                    session_correlation_id = client_cid
                else:
                    # Invalid — reject, keep server-generated, log WARNING
                    logger.warning(
                        "Rejected invalid client correlation_id: %s",
                        client_cid,
                    )

            # Log the command type and correlation_id at INFO
            logger.info(
                "Control command received: %s",
                action,
            )

            # Non-string actions (e.g. a JSON list) are unhashable, so only
            # look strings up; anything else is an unknown action
            handler = _ACTION_HANDLERS.get(action) if isinstance(action, str) else None
            if handler is not None:
                await handler(websocket, data)
            else:
                await websocket.send_json({
                    "type": "error",
                    "payload": {
                        "message": f"Unknown action: {action}",
                    },
                })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        session_error = e
    finally:
        duration_ms = int((time.monotonic() - connect_time) * 1000)
        logger.info(
            "WebSocket session disconnected",
            extra={
                "correlation_id": session_correlation_id,
                "duration_ms": duration_ms,
            },
        )
        if session_error is not None:
            logger.error(f"WebSocket error: {session_error}")
        manager.disconnect(websocket)
