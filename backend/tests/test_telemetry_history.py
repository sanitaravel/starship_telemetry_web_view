"""TelemetryHistory: the current Run's records, kept for replay."""

import json
import logging
import uuid

import pytest

from src.core.telemetry_record import DetectionAccuracy, TelemetryFieldValue, TelemetryRecord
from src.pipeline.history import (
    MAX_HISTORY_RECORDS,
    TelemetryHistory,
    empty_replay_message,
)


def _full_record(seq: int = 1234) -> TelemetryRecord:
    """A record with every field filled in, as large as a real one gets."""
    return TelemetryRecord(
        sequence_number=seq,
        frame_number=98_765,
        mission_elapsed_time="00:02:35",
        mission_elapsed_time_raw="T+00:02:35",
        speed_left=TelemetryFieldValue(value=1523.0, unit="KM/H", status="available"),
        speed_right=TelemetryFieldValue(value=2100.5, unit="KM/H", status="available"),
        altitude_left=TelemetryFieldValue(value=48.2, unit="KM", status="available"),
        altitude_right=TelemetryFieldValue(value=72.0, unit="KM", status="available"),
        stage_left_label="SUPER HEAVY",
        stage_right_label="STARSHIP",
        stage_separation_text="STAGE SEP",
        stage_assignment_left="super_heavy",
        stage_assignment_right="starship",
        separation_state="post_separation",
        t_zero_found=True,
        stage_sep_found=True,
        starship_engines={f"ss_e{i}": "active" for i in range(1, 7)},
        superheavy_engines={f"sh_e{i}": "inactive" for i in range(1, 34)},
        detection_accuracy=DetectionAccuracy(starship=0.83, superheavy=0.97),
        timestamp=1_700_000_000_000,
    )


def _live_text(payload: dict) -> str:
    """The payload's text as broadcast_json serialises the telemetry message."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def test_new_history_is_empty_with_a_uuid4_run_id():
    history = TelemetryHistory()
    snapshot = history.snapshot()
    assert len(history) == 0
    assert snapshot.records == ()
    assert snapshot.truncated is False
    assert uuid.UUID(history.run_id).version == 4
    assert snapshot.run_id == history.run_id


def test_clear_empties_and_gives_a_new_run_id():
    history = TelemetryHistory()
    history.append({"sequence_number": 1})
    old = history.run_id
    new = history.clear()
    assert new == history.run_id != old
    assert uuid.UUID(new).version == 4
    assert len(history) == 0


def test_append_keeps_broadcast_order_as_compact_json():
    history = TelemetryHistory()
    payloads = [{"sequence_number": i, "name": "Ступень"} for i in range(1, 6)]
    for payload in payloads:
        history.append(payload)
    records = history.snapshot().records
    assert records == tuple(_live_text(p) for p in payloads)
    # Compact and not ASCII-escaped, exactly as the live broadcast
    assert records[0] == '{"sequence_number":1,"name":"Ступень"}'


def test_snapshot_is_a_copy():
    history = TelemetryHistory()
    history.append({"sequence_number": 1})
    snapshot = history.snapshot()
    history.append({"sequence_number": 2})
    history.clear()
    assert len(snapshot.records) == 1


def test_full_history_drops_oldest_and_warns_once_per_run(src_caplog):
    history = TelemetryHistory(max_records=3)
    with src_caplog.at_level(logging.WARNING, logger="src.pipeline.history"):
        for i in range(1, 7):
            history.append({"sequence_number": i})
        snapshot = history.snapshot()
        assert [json.loads(r)["sequence_number"] for r in snapshot.records] == [4, 5, 6]
        assert snapshot.truncated is True
        warnings = [r for r in src_caplog.records if r.name == "src.pipeline.history"]
        assert len(warnings) == 1

        # A new Run starts untruncated and warns again once it fills up
        history.clear()
        assert history.snapshot().truncated is False
        for i in range(3):
            history.append({"sequence_number": i})
        assert history.snapshot().truncated is False
        history.append({"sequence_number": 99})
        warnings = [r for r in src_caplog.records if r.name == "src.pipeline.history"]
        assert len(warnings) == 2


def test_invalid_max_records_rejected():
    with pytest.raises(ValueError):
        TelemetryHistory(max_records=0)


def test_default_bound():
    assert TelemetryHistory().snapshot() is not None
    assert MAX_HISTORY_RECORDS == 20_000


def test_stored_record_round_trips_to_the_live_text():
    record = _full_record()
    payload = record.model_dump()
    history = TelemetryHistory()
    history.append(payload)
    stored = history.snapshot().records[0]
    assert stored == _live_text(payload)
    assert TelemetryRecord.model_validate(json.loads(stored)) == record


def test_full_record_size(capsys):
    """Print the compact size of a full record for the design's memory estimate."""
    payload = _full_record().model_dump()
    size = len(_live_text(payload).encode("utf-8"))
    with capsys.disabled():
        print(f"\ncompact TelemetryRecord dump: {size} bytes")
    assert 500 < size < 4_000


def test_empty_replay_message_shape():
    assert empty_replay_message("abc") == {
        "type": "replay",
        "payload": {
            "run_id": "abc",
            "chunk": 0,
            "final": True,
            "truncated": False,
            "records": [],
        },
    }
