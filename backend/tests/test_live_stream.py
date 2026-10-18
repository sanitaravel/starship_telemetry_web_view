"""End-to-end check of the pipeline against a real stream, plus tests of its evaluator.

The live test runs the full pipeline (OCR and engine analysis, no GUI) via
tools/headless_run.py and fails on implausible telemetry. It only runs when
LIVE_STREAM_URL is set, e.g. for a local HLS replay of a flight:

    LIVE_STREAM_URL=http://localhost:8001/output.m3u8 pytest tests/test_live_stream.py -s

LIVE_STREAM_SECONDS (default 60) sets the run length. Output (log, records,
frame previews, summary.json) goes to a pytest tmp dir, printed with -s.
"""

import os

import pytest

from tools.headless_run import RunCapture, evaluate, print_summary, run_pipeline

LIVE_STREAM_URL = os.environ.get("LIVE_STREAM_URL")


@pytest.mark.skipif(not LIVE_STREAM_URL, reason="set LIVE_STREAM_URL to run against a stream")
async def test_live_stream_telemetry_is_plausible(tmp_path):
    seconds = float(os.environ.get("LIVE_STREAM_SECONDS", "60"))
    capture = await run_pipeline(LIVE_STREAM_URL, seconds, skip_frames=30, out_dir=tmp_path)
    summary = evaluate(capture)
    print_summary(summary)
    print(f"\nOutput: {tmp_path}")

    assert summary["records_with_met"] > 0, "no telemetry after T-0"
    assert summary["met_backwards_steps"] == 0, "MET went backwards"
    assert summary["video_seconds_per_wall_second"] >= 1.0, (
        "pipeline is slower than real time at skip_frames=30"
    )
    assert summary["warnings_and_errors"] == {} or all(
        not key.startswith("ERROR") for key in summary["warnings_and_errors"]
    ), summary["warnings_and_errors"]
    flags = {name: items[:5] for name, items in summary["flags"].items()}
    assert not flags, f"implausible readings: {flags}"


# --- Evaluator ---------------------------------------------------------------


def _record(seq, met, speed=None, altitude=None, unit_speed="KM/H", unit_alt="KM", video_s=None):
    """A telemetry record; video_s defaults to just after the MET second (T-0 at 0 s)."""
    if video_s is None:
        h, m, sec = (int(x) for x in met[2:].split(":"))
        video_s = h * 3600 + m * 60 + sec + 0.5

    def gauge(value, unit):
        if value is None:
            return {"value": None, "unit": None, "status": "unavailable"}
        return {"value": value, "unit": unit, "status": "available"}

    return {
        "sequence_number": seq,
        "received_at": float(seq),
        "timestamp": round(video_s * 1000),
        "mission_elapsed_time": met,
        "speed_left": gauge(None, None),
        "altitude_left": gauge(None, None),
        "speed_right": gauge(speed, unit_speed),
        "altitude_right": gauge(altitude, unit_alt),
        "stage_left_label": None,
        "stage_right_label": None,
        "stage_assignment_left": "super_heavy",
        "stage_assignment_right": "super_heavy",
        "separation_state": "pre_separation",
    }


def _capture(records):
    return RunCapture(started_at=0.0, ended_at=float(len(records)), telemetry=records)


def test_evaluator_accepts_a_plausible_ascent():
    records = [
        _record(1, "T+00:00:23", 393, 1.0, video_s=23.2),
        _record(2, "T+00:00:23", 402, 1.1, video_s=23.9),
        _record(3, "T+00:00:27", 444, 1.2),
        _record(4, "T+00:00:29", 506, 1.5),
    ]
    summary = evaluate(_capture(records))
    assert summary["flags"] == {}
    assert summary["fields"]["speed_right"]["available_pct"] == 100.0


def test_evaluator_flags_a_doubled_leading_digit_as_a_spike():
    # 1.1 km read as 41.1 km, as seen on the IFT replay before the fix
    records = [
        _record(1, "T+00:00:22", 380, 1.0),
        _record(2, "T+00:00:23", 402, 41.1),
        _record(3, "T+00:00:24", 415, 1.2),
    ]
    flags = evaluate(_capture(records))["flags"]
    assert [f["value"] for f in flags["altitude_spike"]] == [41.1]
    assert "altitude_step" not in flags


def test_evaluator_flags_a_lasting_jump_as_a_step():
    records = [
        _record(1, "T+00:00:22", 380, 1.0),
        _record(2, "T+00:00:23", 402, 41.1),
        _record(3, "T+00:00:24", 415, 41.2),
    ]
    flags = evaluate(_capture(records))["flags"]
    assert [(f["from"], f["value"]) for f in flags["altitude_step"]] == [(1.0, 41.1)]


def test_evaluator_does_not_compare_across_a_stage_change():
    records = [_record(1, "T+00:02:40", 5000, 70.0), _record(2, "T+00:02:41", 9000, 71.0)]
    records[1]["stage_assignment_right"] = "starship"
    assert evaluate(_capture(records))["flags"] == {}


def test_evaluator_finds_video_gaps_and_misread_clock():
    records = [
        _record(1, "T+00:00:10", 300, 1.0, video_s=10.5),
        _record(2, "T+00:00:11", 310, 1.0, video_s=11.5),
        _record(3, "T+00:00:12", 320, 1.0, video_s=12.5),
        _record(4, "T+00:00:16", 360, 1.1, video_s=16.5),  # three frames lost before it
        _record(5, "T+00:00:47", 370, 1.1, video_s=17.5),  # 17 read as 47
        _record(6, None, 380, 1.2, video_s=18.5),
        _record(7, "T+00:00:17", 390, 1.2, video_s=19.5),  # webcast clock holding
    ]
    summary = evaluate(_capture(records))
    dropped = summary["dropped_frames"]
    assert dropped["frames_missing_in_gaps"] == 3
    assert dropped["video_gaps"][0]["after_seq"] == 3
    assert [m["seq"] for m in summary["clock"]["misread"]] == [5]
    assert [h["seq"] for h in summary["clock"]["holds"]] == [7]
    assert summary["clock"]["unreadable_after_t0"] == 1


def test_evaluator_flags_bad_units():
    records = [_record(1, "T+00:00:10", 300, 0.5, unit_speed="KM")]
    flags = evaluate(_capture(records))["flags"]
    assert flags["unexpected_unit"][0]["unit"] == "KM"


def test_evaluator_flags_readings_credited_to_the_wrong_vehicle():
    records = [_record(1, "T+00:06:58", 4000, 20.0), _record(2, "T+00:06:59", 4000, 20.0)]
    for r in records:
        r["separation_state"] = "post_separation"
        r["stage_right_label"] = "SUPER HEAVY"
    records[0]["stage_assignment_right"] = "starship"
    records[1]["stage_assignment_right"] = "~starship"
    flags = evaluate(_capture(records))["flags"]
    assert [f["seq"] for f in flags["wrong_vehicle"]] == [1]
    assert [f["seq"] for f in flags["unknown_stage"]] == [2]


def test_evaluator_reports_met_going_backwards():
    records = [_record(1, "T+00:00:30", 500, 2.0), _record(2, "T+00:00:20", 510, 2.1)]
    assert evaluate(_capture(records))["met_backwards_steps"] == 1
