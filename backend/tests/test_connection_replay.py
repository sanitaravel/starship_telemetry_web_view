"""ConnectionManager replay on connect: queueing, flushing, dropping and overflow."""

import asyncio
import json
import logging

import pytest
from fastapi import WebSocketDisconnect

from src.api import connections
from src.api.connections import ConnectionManager
from src.api.replay import replay_messages
from src.pipeline.history import TelemetryHistory


class FakeWebSocket:
    """Records what it is sent; every send yields to the event loop first."""

    def __init__(self, fail_on: int | None = None) -> None:
        self.sent: list[str] = []
        self.closed_with: int | None = None
        self._fail_on = fail_on
        self.concurrent_sends = 0
        self._sending = False

    async def accept(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        if self._sending:
            self.concurrent_sends += 1
        self._sending = True
        try:
            await asyncio.sleep(0)
            if self._fail_on is not None and len(self.sent) == self._fail_on:
                raise RuntimeError("client gone")
            self.sent.append(text)
        finally:
            self._sending = False

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    def messages(self) -> list[dict]:
        return [json.loads(t) for t in self.sent]


def _telemetry(seq: int) -> dict:
    return {"type": "telemetry", "payload": {"sequence_number": seq}}


async def _send(cm: ConnectionManager, history: TelemetryHistory, message: dict) -> None:
    """The Broadcaster's send step: append, then broadcast with no await between."""
    if message["type"] == "telemetry":
        history.append(message["payload"])
    await cm.broadcast_json(message)


def _source(history: TelemetryHistory, chunk_size: int = 2):
    return lambda: replay_messages(history.snapshot(), chunk_size=chunk_size)


def _received_seqs(ws: FakeWebSocket) -> list[int]:
    seqs = []
    for m in ws.messages():
        if m["type"] == "replay":
            seqs.extend(r["sequence_number"] for r in m["payload"]["records"])
        elif m["type"] == "telemetry":
            seqs.append(m["payload"]["sequence_number"])
    return seqs


async def test_connect_without_replay_is_immediately_live():
    cm = ConnectionManager()
    ws = FakeWebSocket()
    await cm.connect(ws)
    assert not cm.is_replaying(ws)
    await cm.broadcast("hello")
    assert ws.sent == ["hello"]


async def test_replay_is_sent_first_then_the_client_is_live():
    cm, history = ConnectionManager(), TelemetryHistory()
    for seq in range(1, 6):
        history.append({"sequence_number": seq})
    ws = FakeWebSocket()
    await cm.connect(ws, replay=_source(history))
    assert not cm.is_replaying(ws)
    assert [m["payload"]["chunk"] for m in ws.messages()] == [0, 1, 2]
    await _send(cm, history, _telemetry(6))
    assert _received_seqs(ws) == [1, 2, 3, 4, 5, 6]


async def test_broadcast_during_replay_is_delivered_after_the_last_chunk():
    cm, history = ConnectionManager(), TelemetryHistory()
    for seq in range(1, 7):
        history.append({"sequence_number": seq})
    ws = FakeWebSocket()
    connecting = asyncio.create_task(cm.connect(ws, replay=_source(history)))
    await asyncio.sleep(0)  # accept, snapshot, register, first send started
    assert cm.is_replaying(ws)
    await _send(cm, history, _telemetry(7))
    await _send(cm, history, {"type": "status", "payload": {"status": "running"}})
    await _send(cm, history, _telemetry(8))
    await connecting

    types = [m["type"] for m in ws.messages()]
    assert types == ["replay", "replay", "replay", "telemetry", "status", "telemetry"]
    assert ws.messages()[2]["payload"]["final"] is True
    assert _received_seqs(ws) == list(range(1, 9))
    assert ws.concurrent_sends == 0


async def test_broadcast_during_flush_is_still_delivered_in_order():
    cm, history = ConnectionManager(), TelemetryHistory()
    history.append({"sequence_number": 1})
    ws = FakeWebSocket()
    connecting = asyncio.create_task(cm.connect(ws, replay=_source(history)))
    await asyncio.sleep(0)
    for seq in range(2, 12):
        await _send(cm, history, _telemetry(seq))
        await asyncio.sleep(0)  # let connect() flush some in between
    await connecting
    await _send(cm, history, _telemetry(12))
    assert _received_seqs(ws) == list(range(1, 13))
    assert ws.concurrent_sends == 0


async def test_frame_during_replay_is_not_delivered():
    cm, history = ConnectionManager(), TelemetryHistory()
    history.append({"sequence_number": 1})
    ws = FakeWebSocket()
    live = FakeWebSocket()
    await cm.connect(live)
    connecting = asyncio.create_task(cm.connect(ws, replay=_source(history)))
    await asyncio.sleep(0)
    await _send(cm, history, {"type": "frame", "payload": {"jpeg_base64": "x"}})
    await connecting
    assert "frame" not in [m["type"] for m in ws.messages()]
    assert [m["type"] for m in live.messages()] == ["frame"]

    # Once live, frames are delivered again
    await _send(cm, history, {"type": "frame", "payload": {"jpeg_base64": "y"}})
    assert ws.messages()[-1]["type"] == "frame"


async def test_overflow_closes_the_replaying_client(monkeypatch, src_caplog):
    monkeypatch.setattr(connections, "MAX_PENDING_MESSAGES", 3)
    cm, history = ConnectionManager(), TelemetryHistory()
    history.append({"sequence_number": 1})
    ws = FakeWebSocket()
    connecting = asyncio.create_task(cm.connect(ws, replay=_source(history)))
    await asyncio.sleep(0)
    with src_caplog.at_level(logging.WARNING, logger="src.api.connections"):
        for seq in range(2, 6):
            await _send(cm, history, _telemetry(seq))
    assert ws not in cm.active_connections
    assert not cm.is_replaying(ws)
    assert any("behind" in r.getMessage() for r in src_caplog.records
               if r.levelno == logging.WARNING)

    with pytest.raises(WebSocketDisconnect):
        await connecting
    assert ws.closed_with == 1013
    assert "telemetry" not in [m["type"] for m in ws.messages()]
    assert ws.concurrent_sends == 0


async def test_send_failure_during_replay_propagates_and_disconnect_cleans_up():
    cm, history = ConnectionManager(), TelemetryHistory()
    for seq in range(1, 7):
        history.append({"sequence_number": seq})
    ws = FakeWebSocket(fail_on=1)
    with pytest.raises(RuntimeError):
        await cm.connect(ws, replay=_source(history))
    assert cm.is_replaying(ws)  # the endpoint's finally calls disconnect
    cm.disconnect(ws)
    assert not cm.is_replaying(ws)
    assert ws not in cm.active_connections


async def test_disconnect_during_replay_stops_sending_and_leaves_no_queue():
    cm, history = ConnectionManager(), TelemetryHistory()
    for seq in range(1, 7):
        history.append({"sequence_number": seq})
    ws = FakeWebSocket()
    connecting = asyncio.create_task(cm.connect(ws, replay=_source(history)))
    await asyncio.sleep(0)
    await _send(cm, history, _telemetry(7))
    cm.disconnect(ws)
    await connecting
    assert not cm.is_replaying(ws)
    assert len(ws.sent) < 3
    await _send(cm, history, _telemetry(8))
    assert "telemetry" not in [m["type"] for m in ws.messages()]


async def test_snapshot_and_registration_are_one_step():
    """A record appended right after connect() registers is queued, not lost."""
    cm, history = ConnectionManager(), TelemetryHistory()
    ws = FakeWebSocket()
    connecting = asyncio.create_task(cm.connect(ws, replay=_source(history)))
    await asyncio.sleep(0)
    await _send(cm, history, _telemetry(1))
    await connecting
    messages = ws.messages()
    assert messages[0]["payload"]["records"] == []
    assert _received_seqs(ws) == [1]
