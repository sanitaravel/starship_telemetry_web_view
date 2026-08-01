"""FastAPI WebSocket server and REST endpoints for the Starship Telemetry system.

Provides:
- /ws/telemetry: WebSocket endpoint for real-time telemetry broadcast and control commands
- /api/status: GET endpoint returning pipeline status and GPU capabilities
- /api/validate-url: POST endpoint for livestream URL validation
- /static: Mounted frontend static files
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.gpu_detector import GPUCapabilities, detect_gpu
from src.pipeline import PipelineOrchestrator
from src.template_registry import TemplateRegistry, load_default_template

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
        """Send a text message to all connected clients.

        Removes any clients that fail to receive the message.
        """
        disconnected: list[WebSocket] = []
        for connection in self._active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)

    async def broadcast_json(self, data: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients."""
        disconnected: list[WebSocket] = []
        for connection in self._active_connections:
            try:
                await connection.send_json(data)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)


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
    frame_interval_ms: int
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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns a FastAPI app with all routes registered and static files mounted
    (if the frontend/dist directory exists).
    """
    app = FastAPI(
        title="Starship Telemetry Web View",
        description="Real-time telemetry extraction from SpaceX Starship livestreams",
        version="0.1.0",
    )

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
        await manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_json()
                action = data.get("action")

                if action == "start":
                    source_url = data.get("source_url", "")
                    interval = data.get("interval_ms", 1000)

                    await orchestrator.start(source_url, interval)

                elif action == "stop":
                    orchestrator.stop()

                    # Broadcast status update after stopping
                    await manager.broadcast_json({
                        "type": "status",
                        "payload": orchestrator.build_status_payload(),
                    })

                elif action == "validate_url":
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

                else:
                    await websocket.send_json({
                        "type": "error",
                        "payload": {
                            "message": f"Unknown action: {action}",
                        },
                    })

        except WebSocketDisconnect:
            manager.disconnect(websocket)
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            manager.disconnect(websocket)

    @app.get("/api/status")
    async def get_pipeline_status() -> dict[str, Any]:
        """Return current pipeline status and GPU capabilities.

        Returns:
            Dictionary with status, gpu info, frame_interval_ms, and current_sequence.
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
