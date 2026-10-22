"""FastAPI application factory for the Starship Telemetry system.

Provides:
- /ws/telemetry: WebSocket endpoint for real-time telemetry broadcast and control commands
- /api/status: GET endpoint returning pipeline status and GPU capabilities
- /api/validate-url: POST endpoint for livestream URL validation
- /api/previous-flights: GET endpoint listing available previous flight data
- /api/previous-flights/{name}: GET endpoint serving a specific previous flight JSON
- /api/trajectories: GET endpoint listing recorded ship trajectories
- /api/trajectories/{name}: GET endpoint serving one ship's trajectory points
- /api/missions, /api/missions/{mission_id}: GET/PUT mission settings
- /static: Mounted frontend static files
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from src.api import websocket
from src.api.routes import flights, missions, pipeline, trajectories
from src.version import get_version


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
    # (this file is backend/src/api/app.py, so parents[3] is the repo root)
    frontend_dist = Path(__file__).parents[3] / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/static", StaticFiles(directory=str(frontend_dist)), name="static")

    app.include_router(websocket.router)
    app.include_router(flights.router)
    app.include_router(trajectories.router)
    app.include_router(missions.router)
    app.include_router(pipeline.router)

    return app
