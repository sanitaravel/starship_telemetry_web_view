"""The pipeline feeds the telemetry history: Broadcaster appends, start() resets."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from hypothesis import given, settings, strategies as st

from src.api.connections import ConnectionManager
from src.api.replay import replay_messages
from src.core.gpu import AccelerationBackend, GPUCapabilities
from src.pipeline import PipelineOrchestrator
from src.pipeline.broadcast import Broadcaster
from src.pipeline.history import TelemetryHistory
from src.templates.template_registry import TemplateRegistry, load_default_template


def _telemetry(seq: int) -> dict:
    return {"type": "telemetry", "payload": {"sequence_number": seq}}


def _seqs(history: TelemetryHistory) -> list[int]:
    return [json.loads(r)["sequence_number"] for r in history.snapshot().records]


async def _drain(broadcaster: Broadcaster) -> None:
    """Let the worker send everything queued so far."""
    for _ in range(50):
        await asyncio.sleep(0)
        if broadcaster.queue is None or broadcaster.queue.empty():
            await asyncio.sleep(0)
            return


# --- Broadcaster ---


async def test_telemetry_appended_once_per_send_and_before_the_broadcast():
    history = TelemetryHistory()
    seen_at_send: list[int] = []

    async def broadcast(message):
        seen_at_send.append(len(history))

    broadcaster = Broadcaster(broadcast, history)
    broadcaster.start()
    try:
        for seq in range(1, 4):
            broadcaster.enqueue(_telemetry(seq))
        await _drain(broadcaster)
    finally:
        broadcaster.stop()
    assert _seqs(history) == [1, 2, 3]
    # Each record was already in the history when its broadcast ran
    assert seen_at_send == [1, 2, 3]


async def test_frames_status_and_errors_not_appended():
    history = TelemetryHistory()

    async def broadcast(message):
        pass

    broadcaster = Broadcaster(broadcast, history)
    broadcaster.start()
    try:
        broadcaster.enqueue({"type": "frame", "payload": {"image": "x"}}, droppable=True)
        broadcaster.enqueue({"type": "status", "payload": {}})
        broadcaster.enqueue({"type": "error", "payload": {"message": "x"}})
        broadcaster.enqueue(_telemetry(1))
        await _drain(broadcaster)
    finally:
        broadcaster.stop()
    assert _seqs(history) == [1]


async def test_dropped_previews_and_still_queued_messages_not_appended():
    history = TelemetryHistory()
    sent: list[dict] = []
    gate = asyncio.Event()

    async def broadcast(message):
        sent.append(message)
        await gate.wait()  # a slow client: the queue backs up behind it

    broadcaster = Broadcaster(broadcast, history)
    broadcaster.start()
    broadcaster.enqueue(_telemetry(1))
    await asyncio.sleep(0)  # worker takes #1 and blocks in broadcast
    for seq in range(2, 12):
        broadcaster.enqueue(_telemetry(seq))
    broadcaster.enqueue({"type": "frame", "payload": {}}, droppable=True)  # dropped: queue full
    broadcaster.stop()  # everything still queued belongs to the stopped run
    await asyncio.sleep(0)
    assert _seqs(history) == [1]
    assert [m["type"] for m in sent] == ["telemetry"]


async def test_broadcaster_without_history_still_sends():
    sent: list[dict] = []

    async def broadcast(message):
        sent.append(message)

    broadcaster = Broadcaster(broadcast)
    broadcaster.start()
    try:
        broadcaster.enqueue(_telemetry(1))
        await _drain(broadcaster)
    finally:
        broadcaster.stop()
    assert sent == [_telemetry(1)]


# --- PipelineOrchestrator ---


@pytest.fixture
def orchestrator_and_sent():
    """An orchestrator whose start()/stop() run without capture or EasyOCR."""
    sent: list[dict] = []

    async def broadcast(message):
        sent.append(message)

    registry = TemplateRegistry()
    load_default_template(registry)
    cpu = GPUCapabilities(backend=AccelerationBackend.CPU, device_name=None,
                          cuda_version=None, gpu_available=False)
    orch = PipelineOrchestrator(cpu, registry, broadcast=broadcast)
    orch._frames.ocr_engine = SimpleNamespace(t_zero_detected=False, reset=lambda: None)
    orch._frame_extractor.start = Mock()
    orch._frame_extractor.stop = Mock()
    return orch, sent


async def test_start_broadcasts_empty_replay_before_any_telemetry(orchestrator_and_sent):
    orch, sent = orchestrator_and_sent
    old_run_id = orch.history.run_id

    def extractor_start(config):
        # By the time capture starts, every client has been told to reset,
        # and has the new Run's timeline statuses (all back to not yet)
        assert [m["type"] for m in sent] == ["replay", "timeline_status"]
        assert sent[1]["payload"]["run_id"] == sent[0]["payload"]["run_id"]

    orch._frame_extractor.start.side_effect = extractor_start
    await orch.start("http://example.invalid/stream.m3u8")
    try:
        replay = sent[0]
        assert replay["payload"]["chunk"] == 0
        assert replay["payload"]["final"] is True
        assert replay["payload"]["records"] == []
        assert replay["payload"]["run_id"] == orch.history.run_id != old_run_id
        orch._frame_extractor.start.assert_called_once()

        orch._broadcaster.enqueue(_telemetry(1))
        await _drain(orch._broadcaster)
        types = [m["type"] for m in sent]
        assert types.index("replay") < types.index("telemetry")
    finally:
        await orch.stop()


async def test_history_survives_stop_and_second_start_resets_it(orchestrator_and_sent):
    orch, sent = orchestrator_and_sent
    await orch.start("http://example.invalid/stream.m3u8")
    first_run = orch.history.run_id
    for seq in range(1, 4):
        orch._broadcaster.enqueue(_telemetry(seq))
    await _drain(orch._broadcaster)
    await orch.stop()

    assert _seqs(orch.history) == [1, 2, 3]
    assert orch.history.run_id == first_run

    await orch.start("http://example.invalid/stream.m3u8")
    try:
        assert len(orch.history) == 0
        assert orch.history.run_id != first_run
        replays = [m for m in sent if m["type"] == "replay"]
        assert [m["payload"]["run_id"] for m in replays] == [first_run, orch.history.run_id]
    finally:
        await orch.stop()


async def test_concurrent_starts_give_exactly_one_run(orchestrator_and_sent):
    """A second start() while the first awaits its broadcasts is rejected."""
    orch, sent = orchestrator_and_sent

    async def yielding_broadcast(message):
        await asyncio.sleep(0)  # a real send suspends, opening the race window
        sent.append(message)

    orch._broadcast = yielding_broadcast
    await asyncio.gather(
        orch.start("http://example.invalid/a.m3u8"),
        orch.start("http://example.invalid/b.m3u8"),
    )
    try:
        orch._frame_extractor.start.assert_called_once()
        assert len([m for m in sent if m["type"] == "replay"]) == 1
        assert orch.state.source_url == "http://example.invalid/a.m3u8"
        assert orch._starting is False
    finally:
        await orch.stop()


class _Socket:
    """A websocket that records what it is sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def accept(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        await asyncio.sleep(0)
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        pass

    def messages(self) -> list[dict]:
        return [json.loads(t) for t in self.sent]


def _orchestrator_on(manager: ConnectionManager) -> PipelineOrchestrator:
    registry = TemplateRegistry()
    load_default_template(registry)
    cpu = GPUCapabilities(backend=AccelerationBackend.CPU, device_name=None,
                          cuda_version=None, gpu_available=False)
    orch = PipelineOrchestrator(cpu, registry, broadcast=manager.broadcast_json)
    orch._frames.ocr_engine = SimpleNamespace(t_zero_detected=False, reset=lambda: None)
    orch._frame_extractor.start = Mock()
    orch._frame_extractor.stop = Mock()
    return orch


def _replay_source(orch: PipelineOrchestrator):
    return lambda: replay_messages(orch.history.snapshot(), chunk_size=2)


def _records_of(socket: _Socket) -> list[int]:
    """Sequence numbers received, from replay chunks and live telemetry, in order."""
    seqs = []
    for m in socket.messages():
        if m["type"] == "replay":
            seqs.extend(r["sequence_number"] for r in m["payload"]["records"])
        elif m["type"] == "telemetry":
            seqs.append(m["payload"]["sequence_number"])
    return seqs


@settings(max_examples=25, deadline=None)
@given(
    first_run=st.integers(min_value=0, max_value=6),
    second_run=st.integers(min_value=0, max_value=6),
    clients=st.integers(min_value=0, max_value=3),
)
def test_property_new_run_resets_every_client(first_run, second_run, clients):
    """Property 6: after start(), each client sees an empty chunk 0 with the new run id
    before any telemetry of the Run, and a later client gets only the new Run's records."""

    async def scenario():
        manager = ConnectionManager()
        orch = _orchestrator_on(manager)
        await orch.start("http://example.invalid/a.m3u8")
        for seq in range(1, first_run + 1):
            orch._broadcaster.enqueue(_telemetry(seq))
        await _drain(orch._broadcaster)
        sockets = [_Socket() for _ in range(clients)]
        for s in sockets:
            await manager.connect(s, replay=_replay_source(orch))
        await orch.stop()

        await orch.start("http://example.invalid/b.m3u8")
        new_run_id = orch.history.run_id
        for seq in range(1001, 1001 + second_run):
            orch._broadcaster.enqueue(_telemetry(seq))
        await _drain(orch._broadcaster)
        late = _Socket()
        await manager.connect(late, replay=_replay_source(orch))
        await orch.stop()
        return sockets, late, new_run_id

    sockets, late, new_run_id = asyncio.run(scenario())
    for s in sockets:
        messages = s.messages()
        resets = [i for i, m in enumerate(messages)
                  if m["type"] == "replay" and m["payload"]["run_id"] == new_run_id]
        assert resets, "client never got the new run's reset"
        reset = messages[resets[0]]["payload"]
        assert reset["chunk"] == 0 and reset["records"] == []
        after = messages[resets[0] + 1:]
        assert [m["payload"]["sequence_number"] for m in after if m["type"] == "telemetry"] \
            == list(range(1001, 1001 + second_run))
        assert all(m["payload"]["sequence_number"] < 1001
                   for m in messages[:resets[0]] if m["type"] == "telemetry")
    assert _records_of(late) == list(range(1001, 1001 + second_run))
    assert {m["payload"]["run_id"] for m in late.messages()} == {new_run_id}


@settings(max_examples=25, deadline=None)
@given(count=st.integers(min_value=0, max_value=8))
def test_property_stop_keeps_the_history(count):
    """Property 7: a client connecting after stop() receives every record broadcast."""

    async def scenario():
        manager = ConnectionManager()
        orch = _orchestrator_on(manager)
        await orch.start("http://example.invalid/a.m3u8")
        for seq in range(1, count + 1):
            orch._broadcaster.enqueue(_telemetry(seq))
        await _drain(orch._broadcaster)
        await orch.stop()
        late = _Socket()
        await manager.connect(late, replay=_replay_source(orch))
        return late

    late = asyncio.run(scenario())
    assert _records_of(late) == list(range(1, count + 1))


async def test_starting_guard_cleared_when_start_fails(orchestrator_and_sent):
    orch, _ = orchestrator_and_sent
    orch._frame_extractor.start.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await orch.start("http://example.invalid/a.m3u8")
    assert orch._starting is False
    await orch._frames.close_pools()
    orch._broadcaster.stop()
