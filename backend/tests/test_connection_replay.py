"""ConnectionManager replay on connect: queueing, flushing, dropping and overflow."""

import asyncio
import json
import logging

import pytest
from fastapi import WebSocketDisconnect
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

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


# --- Properties 4 and 5: model-based test of the replay/live handoff ---

_MODEL_MAX_PENDING = 6


class _Client:
    """One fake websocket plus what the model knows about its connection."""

    def __init__(self) -> None:
        self.ws = FakeWebSocket()
        self.task: asyncio.Task | None = None
        self.snapshot_seqs: list[int] | None = None  # history at snapshot time
        self.log_len_at_snapshot: int | None = None  # broadcasts made before it
        self.overflowed = False


class ReplayHandoffMachine(RuleBasedStateMachine):
    """Records, statuses and frames are broadcast by one worker task (as the
    Broadcaster does: append, then broadcast, no await between) while clients
    connect at arbitrary moments and the event loop is stepped a few times."""

    def __init__(self) -> None:
        super().__init__()
        self._patch = pytest.MonkeyPatch()
        self._patch.setattr(connections, "MAX_PENDING_MESSAGES", _MODEL_MAX_PENDING)
        self.loop = asyncio.new_event_loop()
        self.cm = ConnectionManager()
        self.history = TelemetryHistory()
        self.log: list[tuple[str, int]] = []  # every non-frame message, in broadcast order
        self.next_id = 1
        self.clients: list[_Client] = []
        self.queue: asyncio.Queue | None = None
        self.worker: asyncio.Task | None = None
        self.loop.run_until_complete(self._setup())

    async def _setup(self) -> None:
        self.queue = asyncio.Queue()
        self.worker = asyncio.create_task(self._work())

    async def _work(self) -> None:
        while True:
            message = await self.queue.get()
            try:
                # The Broadcaster send step: the log, history append and routing
                # happen together, with no await before broadcast_json routes.
                kind = message["type"]
                if kind != "frame":
                    self.log.append((kind, message["payload"]["id"]))
                if kind == "telemetry":
                    self.history.append(message["payload"])
                await self.cm.broadcast_json(message)
            finally:
                self.queue.task_done()

    def _run(self, coro) -> None:
        self.loop.run_until_complete(coro)

    # --- rules ---

    @rule(kind=st.sampled_from(["telemetry", "telemetry", "telemetry", "status", "frame"]))
    def broadcast(self, kind):
        message_id = self.next_id
        self.next_id += 1
        payload = {"id": message_id}
        if kind == "telemetry":
            payload["sequence_number"] = message_id
        self.queue.put_nowait({"type": kind, "payload": payload})

    @rule(chunk_size=st.integers(min_value=1, max_value=3))
    def connect(self, chunk_size):
        client = _Client()

        def source():
            snapshot = self.history.snapshot()
            client.snapshot_seqs = [json.loads(r)["sequence_number"] for r in snapshot.records]
            client.log_len_at_snapshot = len(self.log)
            return replay_messages(snapshot, chunk_size=chunk_size)

        async def session():
            try:
                await self.cm.connect(client.ws, replay=source)
            except WebSocketDisconnect:
                client.overflowed = True

        async def spawn():
            client.task = asyncio.create_task(session())

        self._run(spawn())
        self.clients.append(client)

    @rule(steps=st.integers(min_value=1, max_value=8))
    def run_loop(self, steps):
        async def spin():
            for _ in range(steps):
                await asyncio.sleep(0)

        self._run(spin())

    # --- invariants ---

    @invariant()
    def replaying_queues_are_bounded_and_frame_free(self):
        for queue in self.cm._pending.values():
            assert len(queue) <= _MODEL_MAX_PENDING
            assert all(json.loads(text)["type"] != "frame" for text in queue)

    @invariant()
    def no_concurrent_sends_to_one_socket(self):
        assert all(c.ws.concurrent_sends == 0 for c in self.clients)

    # --- final checks ---

    async def _drain(self) -> None:
        await asyncio.wait_for(self.queue.join(), timeout=5)
        tasks = [c.task for c in self.clients if c.task is not None]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    def teardown(self) -> None:
        try:
            self._run(self._drain())
            self._check_clients()
        finally:
            self.worker.cancel()
            self.loop.run_until_complete(asyncio.gather(self.worker, return_exceptions=True))
            self.loop.close()
            self._patch.undo()

    def _check_clients(self) -> None:
        last_seq = max((i for kind, i in self.log if kind == "telemetry"), default=None)
        for client in self.clients:
            if client.snapshot_seqs is None:
                continue  # the loop never got far enough to connect it
            if client.overflowed:
                assert client.ws.closed_with == 1013
                assert not self.cm.is_replaying(client.ws)
                assert client.ws not in self.cm.active_connections
                continue
            assert not self.cm.is_replaying(client.ws)
            replayed, live = [], []
            for message in client.ws.messages():
                if message["type"] == "replay":
                    assert not live, "a replay chunk arrived after live messages"
                    replayed.extend(r["sequence_number"] for r in message["payload"]["records"])
                elif message["type"] != "frame":
                    live.append((message["type"], message["payload"]["id"]))
            # The replay is the history at snapshot time, oldest first
            assert replayed == client.snapshot_seqs
            # Everything broadcast after the snapshot arrives once, in order,
            # statuses in their place between the records
            assert live == self.log[client.log_len_at_snapshot:]
            # Together: strictly increasing, gap-free up to the last broadcast
            received = replayed + [i for kind, i in live if kind == "telemetry"]
            assert received == sorted(set(received))
            expected_all = [i for kind, i in self.log if kind == "telemetry"]
            assert received == expected_all


ReplayHandoffMachine.TestCase.settings = settings(
    max_examples=60, stateful_step_count=40, deadline=None
)
TestReplayHandoff = ReplayHandoffMachine.TestCase
