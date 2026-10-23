"""replay_messages: the history snapshot as `replay` chunk texts."""

import json

import pytest

from src.api.replay import REPLAY_CHUNK_SIZE, replay_messages
from src.pipeline.history import HistorySnapshot, TelemetryHistory, empty_replay_message


def _snapshot(n: int, truncated: bool = False, run_id: str = "run-1") -> HistorySnapshot:
    history = TelemetryHistory()
    for i in range(n):
        history.append({"sequence_number": i, "name": "Корабль"})
    return HistorySnapshot(run_id, history.snapshot().records, truncated)


def test_default_chunk_size():
    assert REPLAY_CHUNK_SIZE == 500


def test_empty_snapshot_gives_one_final_chunk_zero():
    texts = list(replay_messages(_snapshot(0)))
    assert len(texts) == 1
    assert json.loads(texts[0]) == empty_replay_message("run-1")
    assert texts[0] == json.dumps(
        empty_replay_message("run-1"), separators=(",", ":"), ensure_ascii=False
    )


def test_1001_records_in_chunks_of_500():
    texts = list(replay_messages(_snapshot(1001), chunk_size=500))
    messages = [json.loads(t) for t in texts]
    assert [m["type"] for m in messages] == ["replay"] * 3
    payloads = [m["payload"] for m in messages]
    assert [p["chunk"] for p in payloads] == [0, 1, 2]
    assert [p["final"] for p in payloads] == [False, False, True]
    assert [len(p["records"]) for p in payloads] == [500, 500, 1]
    assert all(p["run_id"] == "run-1" for p in payloads)
    seqs = [r["sequence_number"] for p in payloads for r in p["records"]]
    assert seqs == list(range(1001))


def test_exact_multiple_has_no_empty_trailing_chunk():
    texts = list(replay_messages(_snapshot(4), chunk_size=2))
    payloads = [json.loads(t)["payload"] for t in texts]
    assert [p["final"] for p in payloads] == [False, True]


def test_records_are_the_stored_texts_unchanged():
    snapshot = _snapshot(3)
    (text,) = replay_messages(snapshot)
    assert ('"records":[' + ",".join(snapshot.records) + "]") in text


@pytest.mark.parametrize("truncated", [False, True])
@pytest.mark.parametrize("n", [0, 1, 7])
def test_truncated_passes_through(n, truncated):
    for text in replay_messages(_snapshot(n, truncated), chunk_size=3):
        assert json.loads(text)["payload"]["truncated"] is truncated


def test_run_id_is_json_escaped():
    (text,) = replay_messages(_snapshot(1, run_id='a"b'))
    assert json.loads(text)["payload"]["run_id"] == 'a"b'


def test_is_lazy():
    texts = replay_messages(_snapshot(10), chunk_size=1)
    assert json.loads(next(texts))["payload"]["chunk"] == 0


def test_invalid_chunk_size_rejected():
    with pytest.raises(ValueError):
        list(replay_messages(_snapshot(1), chunk_size=0))
