"""Mission settings endpoints: /api/missions[/{mission_id}].

One JSON file per mission in missions/ (see missions.py). Saving the mission
whose timeline is being followed (see routes/timeline.py) re-reads it.
"""

from typing import Any

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from src.api.routes.timeline import broadcast_timeline
from src.api.runtime import orchestrator
from src.storage import missions
from src.storage.missions import TimelineEvent

router = APIRouter()


@router.get("/api/missions")
def list_missions() -> list[dict[str, Any]]:
    """List saved missions as ``{id, mission, ship, updated_at}``, newest first."""
    return missions.list_missions(missions.MISSIONS_DIR)


@router.get("/api/missions/{mission_id}")
def get_mission(mission_id: str) -> Any:
    """Serve one mission's settings, engine serials included."""
    record = missions.load_mission(mission_id, missions.MISSIONS_DIR)
    if record is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Mission '{mission_id}' not found."},
        )
    return record


@router.put("/api/missions/{mission_id}")
async def put_mission(mission_id: str, settings: missions.MissionSettings) -> Any:
    """Save a mission's settings to its file, replacing what was there.

    The id must be the one the mission name maps to (``IFT 14`` ->
    ``ift-14``), so a mission always lives in the file named after it.
    The file is written in the threadpool; if it is the active mission, its
    timeline is followed from the saved version on.
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
    saved = await run_in_threadpool(missions.save_mission, settings, missions.MISSIONS_DIR)
    if orchestrator.timeline.mission_id == mission_id:
        events = [TimelineEvent.model_validate(e) for e in saved["timeline"]]
        if orchestrator.timeline.set_mission(mission_id, events):
            await broadcast_timeline()
    return saved
