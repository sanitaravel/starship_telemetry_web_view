"""Mission timeline status endpoints: /api/timeline/...

- ``GET /api/timeline/status``: the full status snapshot (what ``timeline_status`` carries).
- ``PUT /api/timeline/active`` ``{mission_id}``: which mission the run is, so whose
  timeline is followed (None: none). A mission without a file has an empty timeline
  until its file is saved.
- ``POST /api/timeline/{event_id}/status`` ``{status}``: a person sets an event's
  status, or with ``null`` hands it back to auto.

Every change is broadcast as a ``timeline_status`` message. The handlers are
``async`` so the status machine is only touched on the event loop; the
mission file is read in the threadpool.
"""

import re
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from src.api.runtime import manager, orchestrator
from src.timeline.service import read_mission_timeline

router = APIRouter()

_MISSION_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ActiveMissionRequest(BaseModel):
    """Body of PUT /api/timeline/active."""

    mission_id: str | None

    @field_validator("mission_id")
    @classmethod
    def _valid_id(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > 64 or not _MISSION_ID.match(value)):
            raise ValueError(f"invalid mission id {value!r}")
        return value


class StatusOverrideRequest(BaseModel):
    """Body of POST /api/timeline/{event_id}/status; null hands the event back to auto."""

    status: Literal["not_yet", "pending", "success", "partial", "failure", "skipped"] | None


async def broadcast_timeline() -> None:
    """Send the current statuses to every client."""
    await manager.broadcast_json(orchestrator.timeline.message())


async def activate_mission(mission_id: str | None) -> None:
    """Follow mission_id's timeline (read from its file) and broadcast if that changed anything."""
    events = [] if mission_id is None else await run_in_threadpool(read_mission_timeline, mission_id)
    if orchestrator.timeline.set_mission(mission_id, events):
        await broadcast_timeline()


@router.get("/api/timeline/status")
async def get_timeline_status() -> dict[str, Any]:
    """The active mission's timeline statuses for the current run."""
    return orchestrator.timeline.snapshot()


@router.put("/api/timeline/active")
async def put_active_mission(request: ActiveMissionRequest) -> dict[str, Any]:
    """Follow this mission's timeline; returns the resulting snapshot."""
    await activate_mission(request.mission_id)
    return orchestrator.timeline.snapshot()


@router.post("/api/timeline/{event_id}/status")
async def post_event_status(event_id: str, request: StatusOverrideRequest) -> Any:
    """Override an event's status (or clear the override); returns the resulting snapshot."""
    try:
        changed = orchestrator.timeline.override(event_id, request.status)
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No timeline event '{event_id}'."},
        )
    if changed:
        await broadcast_timeline()
    return orchestrator.timeline.snapshot()
