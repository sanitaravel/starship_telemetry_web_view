"""replay_messages: the history snapshot as `replay` chunk texts."""

import json

import pytest
from hypothesis import given, settings, strategies as st

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


@settings(max_examples=150, deadline=None)
@given(
    records=st.lists(st.integers(min_value=0, max_value=10**6), max_size=60),
    chunk_size=st.integers(min_value=1, max_value=70),
    truncated=st.booleans(),
    run_id=st.text(max_size=12),
)
def test_property_chunking_is_lossless(records, chunk_size, truncated, run_id):
    """Property 3: ceil(n/size) (at least 1) valid chunks, numbered, one final, same records."""
    stored = tuple(json.dumps({"sequence_number": r}, separators=(",", ":")) for r in records)
    snapshot = HistorySnapshot(run_id, stored, truncated)
    texts = list(replay_messages(snapshot, chunk_size=chunk_size))
    expected = max(1, -(-len(records) // chunk_size))
    assert len(texts) == expected
    assert all(json.loads(t)["type"] == "replay" for t in texts)
    payloads = [json.loads(t)["payload"] for t in texts]
    assert [p["chunk"] for p in payloads] == list(range(expected))
    assert [p["final"] for p in payloads] == [False] * (expected - 1) + [True]
    assert all(len(p["records"]) <= chunk_size for p in payloads)
    assert all(p["run_id"] == run_id and p["truncated"] is truncated for p in payloads)
    assert [r["sequence_number"] for p in payloads for r in p["records"]] == records
