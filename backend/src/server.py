"""FastAPI WebSocket server and REST endpoints for the Starship Telemetry system.

Provides:
- /ws/telemetry: WebSocket endpoint for real-time telemetry broadcast and control commands
- /api/status: GET endpoint returning pipeline status and GPU capabilities
- /api/validate-url: POST endpoint for livestream URL validation
- /api/previous-flights: GET endpoint listing available previous flight data
- /api/previous-flights/{name}: GET endpoint serving a specific previous flight JSON
- /api/trajectories: GET endpoint listing recorded ship trajectories
- /api/trajectories/{name}: GET endpoint serving one ship's trajectory points
- /static: Mounted frontend static files
"""

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.gpu_detector import GPUCapabilities, detect_gpu
from src.logging_config import configure_logging
from src.logging_context import correlation_id_var
from src.pipeline import PipelineOrchestrator
from src.template_registry import TemplateRegistry, load_default_template
from src.version import get_version
from src import missions, previous_flights, trajectories

# Initialize structured logging before any HTTP/WebSocket processing
configure_logging()

logger = logging.getLogger(__name__)

# UUID v4 lowercase hyphenated format validation pattern
_UUID4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _is_valid_uuid4(value: str) -> bool:
    """Check if a string is a valid UUID v4 in lowercase hyphenated format."""
    return bool(_UUID4_PATTERN.match(value))


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


class ValidateUrlRequest(BaseModel):
    """Request body for the /api/validate-url endpoint."""

    url: str


class ValidateUrlResponse(BaseModel):
    """Response body for the /api/validate-url endpoint."""

    valid: bool
    message: str
    url: str


class PipelineStatusResponse(BaseModel):
    """Response body for the /api/status endpoint."""

    status: str
    gpu: dict[str, Any]
    skip_frames: int
    current_sequence: int


# --- Application state ---

manager = ConnectionManager()
gpu_capabilities: GPUCapabilities = detect_gpu()
template_registry = TemplateRegistry()

# Load the default ROI template
_load_error = load_default_template(template_registry)
if _load_error is not None:
    logger.warning(f"Failed to load default ROI template: {_load_error.message}")

# Create the pipeline orchestrator, wiring broadcast through the connection manager
orchestrator = PipelineOrchestrator(
    gpu_capabilities=gpu_capabilities,
    template_registry=template_registry,
    broadcast=manager.broadcast_json,
)


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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns a FastAPI app with all routes registered and static files mounted
    (if the frontend/dist directory exists).
    """
    app = FastAPI(
        title="Starship Telemetry Web View",
        description="Real-time telemetry extraction from SpaceX Starship livestreams",
        version=get_version(),
    )

    # Compress HTTP responses (flight data is JSON and shrinks ~10x).
    # WebSocket traffic is not affected.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # Mount frontend static files if the directory exists
    frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/static", StaticFiles(directory=str(frontend_dist)), name="static")

    @app.websocket("/ws/telemetry")
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

    # --- Previous Flights Endpoints ---
    # Plain `def` handlers run in the threadpool, so reading a flight file
    # never blocks the event loop (and the live telemetry stream with it).

    @app.get("/api/previous-flights")
    def list_previous_flights() -> list[dict[str, str]]:
        """List available previous flight data files.

        Returns:
            List of objects with 'name' (display name) and 'filename' fields.
        """
        return previous_flights.list_flights(previous_flights.PREVIOUS_FLIGHTS_DIR)

    @app.get("/api/previous-flights/{name}")
    def get_previous_flight(name: str) -> Response:
        """Serve a previous flight as columnar JSON.

        Args:
            name: The filename stem (e.g., 'ift-13') of the flight data.

        Returns:
            ``{"met": [...], "t": [...], "ss_speed": [...], "sh_speed": [...],
            "ss_alt": [...], "sh_alt": [...]}``, one entry per record.
        """
        payload = previous_flights.load_flight(name, previous_flights.PREVIOUS_FLIGHTS_DIR)
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"Flight data '{name}' not found."},
            )
        return Response(content=payload, media_type="application/json")

    # --- Ship Trajectory Endpoints ---

    @app.get("/api/trajectories")
    def list_trajectories() -> list[dict[str, Any]]:
        """List recorded ship trajectories from the trajectories/ directory."""
        return trajectories.list_trajectories(trajectories.TRAJECTORIES_DIR)

    @app.get("/api/trajectories/{name}")
    def get_trajectory(name: str) -> Any:
        """Serve one ship's trajectory (e.g. 'ship39') as a list of points."""
        points = trajectories.load_trajectory(name, trajectories.TRAJECTORIES_DIR)
        if points is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"Trajectory '{name}' not found."},
            )
        return points

    # --- Mission Settings Endpoints ---
    # One JSON file per mission in missions/ (see missions.py).

    @app.get("/api/missions")
    def list_missions() -> list[dict[str, Any]]:
        """List saved missions as ``{id, mission, ship, updated_at}``, newest first."""
        return missions.list_missions(missions.MISSIONS_DIR)

    @app.get("/api/missions/{mission_id}")
    def get_mission(mission_id: str) -> Any:
        """Serve one mission's settings, engine serials included."""
        record = missions.load_mission(mission_id, missions.MISSIONS_DIR)
        if record is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"Mission '{mission_id}' not found."},
            )
        return record

    @app.put("/api/missions/{mission_id}")
    def put_mission(mission_id: str, settings: missions.MissionSettings) -> Any:
        """Save a mission's settings to its file, replacing what was there.

        The id must be the one the mission name maps to (``IFT 14`` ->
        ``ift-14``), so a mission always lives in the file named after it.
        """
        expected = missions.mission_id(settings.mission)
        if mission_id != expected:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": f"Mission '{settings.mission}' is saved as '{expected}', "
                    f"not '{mission_id}'."
                },
            )
        return missions.save_mission(settings, missions.MISSIONS_DIR)

    @app.get("/api/status")
    async def get_pipeline_status() -> dict[str, Any]:
        """Return current pipeline status and GPU capabilities.

        Returns:
            Dictionary with status, gpu info, skip_frames, current_sequence,
            the parallel-processing counters and the backend version.
        """
        return orchestrator.build_status_payload()

    @app.post("/api/validate-url")
    async def validate_url(request: ValidateUrlRequest) -> ValidateUrlResponse:
        """Validate a livestream URL is reachable.

        Args:
            request: Request body containing the URL to validate.

        Returns:
            Validation result with valid flag, message, and the URL.
        """
        url = request.url
        if not url or not url.strip():
            return ValidateUrlResponse(
                valid=False,
                message="URL cannot be empty.",
                url=url,
            )

        error = await orchestrator.frame_extractor.validate(url)
        if error is None:
            return ValidateUrlResponse(
                valid=True,
                message="URL is reachable and active.",
                url=url,
            )
        return ValidateUrlResponse(
            valid=False,
            message=error.message,
            url=url,
        )

    return app


# Create the default app instance for direct use with uvicorn
app = create_app()
