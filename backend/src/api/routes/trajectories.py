"""Ship trajectory endpoints: /api/trajectories[/{name}]."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src import trajectories

router = APIRouter()


@router.get("/api/trajectories")
def list_trajectories() -> list[dict[str, Any]]:
    """List recorded ship trajectories from the trajectories/ directory."""
    return trajectories.list_trajectories(trajectories.TRAJECTORIES_DIR)


@router.get("/api/trajectories/{name}")
def get_trajectory(name: str) -> Any:
    """Serve one ship's trajectory (e.g. 'ship39') as a list of points."""
    points = trajectories.load_trajectory(name, trajectories.TRAJECTORIES_DIR)
    if points is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Trajectory '{name}' not found."},
        )
    return points
