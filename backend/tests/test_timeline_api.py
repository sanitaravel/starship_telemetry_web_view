"""Timeline statuses end to end: service, orchestrator feed, REST routes, WebSocket and saved flights."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.routes.timeline import StatusOverrideRequest
from src.api.runtime import orchestrator
from src.core.gpu import AccelerationBackend, GPUCapabilities
from src.pipeline import PipelineOrchestrator
from src.storage import missions, previous_flights
from src.timeline.service import TimelineService, read_mission_timeline
from src.timeline.status import STATUSES
from src.templates.template_registry import TemplateRegistry, load_default_template
from tests.timeline_records import record

TIMELINE = [
    {"id": "liftoff", "t": 0, "event": "Liftoff", "vehicle": "both", "auto": {"kind": "rule", "rule": "liftoff"}},
    {"id": "maxq", "t": 60, "event": "Max Q", "vehicle": "both", "auto": {"kind": "hud", "label": "MAX Q"}},
    {"id": "go", "t": -60, "event": "Go for launch", "vehicle": "both", "auto": {"kind": "manual"}},
]


def save_mission(directory: Path, name: str = "IFT 14", timeline=TIMELINE) -> None:
    missions.save_mission(missions.MissionSettings(mission=name, timeline=timeline), directory)


@pytest.fixture
def missions_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(missions, "MISSIONS_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def clean_timeline():
    """The runtime orchestrator is a process singleton: leave its timeline as found."""
    yield
    orchestrator.timeline.set_mission(None, [])
    orchestrator.timeline.reset_run(orchestrator.history.run_id)


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# --- Service ---


def test_service_revision_grows_with_every_change():
    service = TimelineService()
    service.reset_run("run-1")
    first = service.snapshot()["revision"]
    assert service.set_mission("ift-14", read_events())
    assert not service.on_met(-120)  # nothing due yet
    assert service.on_met(-60)  # the manual event is now pending
    snap = service.snapshot()
    assert snap["revision"] > first
    assert (snap["mission_id"], snap["run_id"], snap["pending"], snap["met"]) == ("ift-14", "run-1", 1, -60.0)
    assert [e["event_id"] for e in snap["events"]] == ["go", "liftoff", "maxq"]
    assert set(snap["events"][0]) == {"event_id", "status", "source", "evidence", "met", "updated_at"}


def read_events():
    return [missions.TimelineEvent.model_validate(e) for e in TIMELINE]


def test_read_mission_timeline(missions_dir: Path):
    assert read_mission_timeline("ift-14") == []
    save_mission(missions_dir)
    assert [e.id for e in read_mission_timeline("ift-14")] == ["go", "liftoff", "maxq"]


def test_override_body_allows_exactly_the_statuses():
    assert StatusOverrideRequest.model_json_schema()["properties"]["status"]["anyOf"][0]["enum"] == list(STATUSES)


# --- Orchestrator ---


@pytest.fixture
def orchestrator_and_sent():
    sent: list[dict] = []

    async def broadcast(message):
        sent.append(message)

    registry = TemplateRegistry()
    load_default_template(registry)
    cpu = GPUCapabilities(backend=AccelerationBackend.CPU, device_name=None, cuda_version=None, gpu_available=False)
    orch = PipelineOrchestrator(cpu, registry, broadcast=broadcast)
    orch._frames.ocr_engine = SimpleNamespace(t_zero_detected=False, reset=lambda: None)
    orch._frame_extractor.start = Mock()
    orch._frame_extractor.stop = Mock()
    return orch, sent


async def _drain(orch) -> None:
    for _ in range(50):
        await asyncio.sleep(0)


async def test_broadcast_records_feed_the_timeline_and_its_changes_follow_them(orchestrator_and_sent):
    orch, sent = orchestrator_and_sent
    orch.timeline.set_mission("ift-14", read_events())
    await orch.start("http://example.invalid/stream.m3u8")
    try:
        assert [m["type"] for m in sent] == ["replay", "timeline_status", "status"]
        assert all(e["status"] == "not_yet" for e in sent[1]["payload"]["events"])
        orch._broadcaster.enqueue({"type": "telemetry", "payload": record(0, sh=33)})
        orch._broadcaster.enqueue({"type": "telemetry", "payload": record(1, sh=33)})
        await _drain(orch)
    finally:
        await orch.stop()
    # The first record decides liftoff and makes the countdown's manual event pending;
    # the second changes nothing, so it brings no timeline_status
    assert [m["type"] for m in sent][3:] == ["telemetry", "timeline_status", "telemetry"]
    statuses = {e["event_id"]: e["status"] for e in sent[4]["payload"]["events"]}
    assert statuses == {"go": "pending", "liftoff": "success", "maxq": "not_yet"}


async def test_a_new_run_resets_overrides(orchestrator_and_sent):
    orch, sent = orchestrator_and_sent
    orch.timeline.set_mission("ift-14", read_events())
    orch.timeline.override("maxq", "skipped")
    await orch.start("http://example.invalid/stream.m3u8")
    await orch.stop()
    assert orch.timeline.machine.status("maxq").source == "auto"
    assert orch.timeline.snapshot()["run_id"] == orch.history.run_id


# --- REST ---


async def test_get_status_snapshot(client: AsyncClient):
    response = await client.get("/api/timeline/status")
    assert response.status_code == 200
    body = response.json()
    assert body["mission_id"] is None and body["events"] == []
    assert body["run_id"] == orchestrator.history.run_id


async def test_activate_mission_loads_its_timeline_and_broadcasts(client: AsyncClient, missions_dir: Path):
    save_mission(missions_dir)
    with patch("src.api.routes.timeline.manager.broadcast_json", new=AsyncMock()) as sent:
        response = await client.put("/api/timeline/active", json={"mission_id": "ift-14"})
    assert response.status_code == 200
    assert [e["event_id"] for e in response.json()["events"]] == ["go", "liftoff", "maxq"]
    sent.assert_awaited_once()
    assert sent.await_args.args[0]["type"] == "timeline_status"


async def test_activate_mission_without_a_file_follows_an_empty_timeline(client: AsyncClient, missions_dir: Path):
    response = await client.put("/api/timeline/active", json={"mission_id": "ift-99"})
    assert response.status_code == 200
    assert (response.json()["mission_id"], response.json()["events"]) == ("ift-99", [])
    response = await client.put("/api/timeline/active", json={"mission_id": None})
    assert response.json()["mission_id"] is None


async def test_activate_rejects_an_invalid_id(client: AsyncClient):
    response = await client.put("/api/timeline/active", json={"mission_id": "../etc"})
    assert response.status_code == 422


async def test_saving_the_active_mission_reloads_its_timeline(client: AsyncClient, missions_dir: Path):
    save_mission(missions_dir)
    await client.put("/api/timeline/active", json={"mission_id": "ift-14"})
    body = {"mission": "IFT 14", "timeline": TIMELINE[:1]}
    with patch("src.api.routes.timeline.manager.broadcast_json", new=AsyncMock()) as sent:
        response = await client.put("/api/missions/ift-14", json=body)
    assert response.status_code == 200
    assert [e.id for e in orchestrator.timeline.machine.events] == ["liftoff"]
    sent.assert_awaited_once()


async def test_saving_another_mission_leaves_the_active_one(client: AsyncClient, missions_dir: Path):
    save_mission(missions_dir)
    await client.put("/api/timeline/active", json={"mission_id": "ift-14"})
    response = await client.put("/api/missions/ift-15", json={"mission": "IFT 15"})
    assert response.status_code == 200
    assert len(orchestrator.timeline.machine.events) == 3


async def test_override_and_back_to_auto(client: AsyncClient, missions_dir: Path):
    save_mission(missions_dir)
    await client.put("/api/timeline/active", json={"mission_id": "ift-14"})
    with patch("src.api.routes.timeline.manager.broadcast_json", new=AsyncMock()) as sent:
        response = await client.post("/api/timeline/maxq/status", json={"status": "skipped"})
        assert response.status_code == 200
        maxq = next(e for e in response.json()["events"] if e["event_id"] == "maxq")
        assert (maxq["status"], maxq["source"]) == ("skipped", "human")
        response = await client.post("/api/timeline/maxq/status", json={"status": None})
        maxq = next(e for e in response.json()["events"] if e["event_id"] == "maxq")
        assert (maxq["status"], maxq["source"]) == ("not_yet", "auto")
    assert sent.await_count == 2


async def test_override_unknown_event_is_404_and_bad_status_422(client: AsyncClient, missions_dir: Path):
    save_mission(missions_dir)
    await client.put("/api/timeline/active", json={"mission_id": "ift-14"})
    assert (await client.post("/api/timeline/nope/status", json={"status": "success"})).status_code == 404
    assert (await client.post("/api/timeline/maxq/status", json={"status": "great"})).status_code == 422
    assert (await client.post("/api/timeline/maxq/status", json={})).status_code == 422


# --- WebSocket ---


def test_override_reaches_a_connected_client_and_a_late_one_gets_the_latest(app, missions_dir: Path):
    save_mission(missions_dir)
    with TestClient(app) as http, http.websocket_connect("/ws/telemetry") as ws:
        while ws.receive_json()["type"] != "timeline_status":
            pass
        http.put("/api/timeline/active", json={"mission_id": "ift-14"})
        assert ws.receive_json()["payload"]["mission_id"] == "ift-14"
        http.post("/api/timeline/go/status", json={"status": "success"})
        live = ws.receive_json()
        assert live["type"] == "timeline_status"
        with http.websocket_connect("/ws/telemetry") as late:
            while (message := late.receive_json())["type"] != "timeline_status":
                pass
    assert message == live
    go = next(e for e in message["payload"]["events"] if e["event_id"] == "go")
    assert (go["status"], go["source"]) == ("success", "human")


# --- Saved flights ---


def test_flight_file_with_timeline_statuses_still_loads(tmp_path: Path):
    flight_record = {
        "frame_number": 1,
        "vehicles": {"starship": {"speed": 1.0, "altitude": 0.1, "engines": {}},
                     "superheavy": {"speed": 1.0, "altitude": 0.1, "engines": {}}},
        "time": {"sign": "+", "hours": 0, "minutes": 0, "seconds": 1},
        "real_time_seconds": 31.0,
    }
    statuses = {"mission_id": "ift-14", "events": [{"event_id": "liftoff", "status": "success",
                                                     "source": "auto", "evidence": "T-0", "met": 0.0,
                                                     "updated_at": "2026-10-24T00:00:00Z"}]}
    (tmp_path / "ift-14.json").write_text(json.dumps({"records": [flight_record], "timeline_status": statuses}))
    (tmp_path / "ift-13.json").write_text(json.dumps([flight_record]))
    wrapped = json.loads(previous_flights.load_flight("ift-14", tmp_path))
    plain = json.loads(previous_flights.load_flight("ift-13", tmp_path))
    assert wrapped["met"] == plain["met"] == ["+00:00:01"]
    assert wrapped["timeline_status"] == statuses
    assert "timeline_status" not in plain
