"""Tests for scripts/downsample_telemetry.py."""

import importlib.util
import json

import pytest

from src.core.version import REPO_ROOT


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "downsample_telemetry", REPO_ROOT / "scripts" / "downsample_telemetry.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ds = _load_script()


def _records(frames):
    return [{"frame_number": f, "vehicles": {"starship": {"speed": f}}} for f in frames]


def test_keeps_every_nth_analysed_frame_by_frame_number():
    # Analysed every 10th frame, stored out of order as the real files are
    frames = [105, 115, 125, 135, 145, 155, 165, 85, 95]
    result, stats = ds.downsample(_records(frames), factor=3)
    assert [r["frame_number"] for r in result] == [85, 115, 145]
    assert (stats["frame_step"], stats["stride"], stats["output"]) == (10, 30, 3)


def test_frame_step_ignores_gaps_from_dropped_frames():
    assert ds.detect_frame_step([0, 10, 20, 130, 140, 150, 360]) == 10


def test_factor_one_keeps_everything_sorted():
    result, _ = ds.downsample(_records([30, 10, 20]), factor=1)
    assert [r["frame_number"] for r in result] == [10, 20, 30]


def test_duplicates_and_records_without_frame_number():
    records = _records([0, 10, 20, 30]) + [{"frame_number": 0, "dup": True}, {"vehicles": {}}]
    result, stats = ds.downsample(records, factor=3)
    assert [r["frame_number"] for r in result] == [0, 30]
    assert "dup" not in result[0]
    assert stats["without_frame_number"] == 1


def test_cli_writes_next_to_input_and_keeps_the_original(tmp_path, capsys):
    source = tmp_path / "ift-99.json"
    source.write_text(json.dumps(_records(range(0, 100, 10)), indent=4), encoding="utf-8")
    before = source.read_text(encoding="utf-8")

    assert ds.main([str(source)]) == 0

    output = tmp_path / "ift-99.every3.json"
    assert [r["frame_number"] for r in json.loads(output.read_text(encoding="utf-8"))] == [0, 30, 60, 90]
    assert output.read_text(encoding="utf-8").startswith("[\n    {")
    assert source.read_text(encoding="utf-8") == before
    assert "10 records" in capsys.readouterr().out


def test_cli_keeps_a_files_timeline_statuses(tmp_path):
    source = tmp_path / "ift-99.json"
    statuses = {"mission_id": "ift-99", "events": []}
    source.write_text(json.dumps({"records": _records(range(0, 100, 10)), "timeline_status": statuses}), encoding="utf-8")
    assert ds.main([str(source)]) == 0
    written = json.loads((tmp_path / "ift-99.every3.json").read_text(encoding="utf-8"))
    assert written["timeline_status"] == statuses
    assert [r["frame_number"] for r in written["records"]] == [0, 30, 60, 90]


def test_cli_refuses_to_overwrite_the_input(tmp_path):
    source = tmp_path / "f.json"
    source.write_text(json.dumps(_records([0, 10, 20])), encoding="utf-8")
    with pytest.raises(SystemExit):
        ds.main([str(source), "--output", str(source)])


# --- Filtering ----------------------------------------------------------------


def test_strip_leading_digit():
    assert ds.strip_leading_digit(3158.0) == 158.0
    assert ds.strip_leading_digit(20.2) == 0.2
    assert ds.strip_leading_digit(718.0) == 18.0
    assert ds.strip_leading_digit(1.5) is None
    assert ds.strip_leading_digit(7.0) is None


def test_clean_series_repairs_extra_leading_digits():
    # IFT-14 at T+10..16: speed with a stray leading digit on some readings
    times = [10, 11, 12, 13, 14, 15, 16]
    values = [1116.0, 128.0, 1146.0, 164.0, 3182.0, 201.0, 4220.0]
    cleaned, repaired, removed = ds.clean_series(times, values, ds.LIMITS["speed"])
    assert cleaned == [116.0, 128.0, 146.0, 164.0, 182.0, 201.0, 220.0]
    assert (repaired, removed) == (4, 0)


def test_clean_series_repairs_a_long_wrong_stretch():
    # IFT-14 at T+7:50: altitude read as 3158.. for a long stretch, then right
    times = list(range(20))
    values = [157.0] + [3158.0 + i * 0.1 for i in range(15)] + [160.0, 160.1, 160.2, 160.3]
    cleaned, repaired, _ = ds.clean_series(times, values, ds.LIMITS["altitude"])
    assert all(v < 200 for v in cleaned)
    assert repaired == 15


def test_clean_series_keeps_a_good_series_untouched():
    times = list(range(10))
    values = [100.0 + 30 * t for t in times]
    assert ds.clean_series(times, values, ds.LIMITS["speed"]) == (values, 0, 0)


def test_clean_series_drops_isolated_garbage_instead_of_rewriting_good_readings():
    # A good series with one reading that fits neither as read nor repaired
    times = list(range(120))
    values = [50.0 + 0.1 * t for t in times]
    values[60] = 95.0
    cleaned, repaired, dropped = ds.clean_series(times, values, ds.LIMITS["altitude"])
    assert cleaned[60] is None
    assert [v for i, v in enumerate(cleaned) if i != 60] == [v for i, v in enumerate(values) if i != 60]
    assert (repaired, dropped) == (0, 1)


def test_clean_series_drops_long_runs_that_are_out_of_range_either_way():
    # 9999 km is out of range as read and as 999; a run longer than _MAX_SKIP_S
    times = list(range(40))
    values = [150.0 + 0.1 * t for t in times]
    for i in range(10, 22):
        values[i] = 9999.0
    cleaned, _, dropped = ds.clean_series(times, values, ds.LIMITS["altitude"])
    assert cleaned[:10] == values[:10] and cleaned[22:] == values[22:]
    assert all(v is None for v in cleaned[10:22])
    assert dropped == 12


def test_clean_series_keeps_everything_before_an_unavoidable_break():
    # A genuine jump no reading can explain must not make the programme drop
    # all the readings before it (it once dropped the whole IFT-14 ascent)
    times = list(range(400))
    # 12000.. after 2990: neither as read nor as 2000.. does it continue
    values = [10.0 * t for t in times[:300]] + [9_000.0 + 10.0 * t for t in times[300:]]
    cleaned, _, dropped = ds.clean_series(times, values, ds.LIMITS["speed"])
    assert cleaned == values
    assert dropped == 0


def test_clean_series_bridges_a_gap_in_the_data():
    # Readings 60 s apart (frames lost) are compared over the full gap
    times = [0, 1, 2, 62, 63]
    values = [100.0, 130.0, 160.0, 2000.0, 2030.0]
    assert ds.clean_series(times, values, ds.LIMITS["speed"]) == (values, 0, 0)


def test_filter_records_uses_a_zero_frame_clock_as_a_real_time():
    # IFT-14 starts at real_time_seconds 0.0; treating it as missing put the
    # first record ~2000 s away and wiped out the ascent
    def record(frame, seconds, speed):
        return {
            "frame_number": frame,
            "real_time_seconds": seconds,
            "vehicles": {
                "starship": {"speed": speed, "altitude": None},
                "superheavy": {"speed": None, "altitude": None},
            },
        }

    speeds = [0, 718.0, 36, 54, 72, 90, 108, 126, 4144.0, 162]  # two extra digits
    records = [record(64465 + 30 * i, float(i), s) for i, s in enumerate(speeds)]
    stats = ds.filter_records(records)
    assert [r["vehicles"]["starship"]["speed"] for r in records] == [
        0, 18.0, 36, 54, 72, 90, 108, 126, 144.0, 162
    ]
    assert stats["starship.speed"]["removed"] == 0


def test_filter_records_cleans_each_vehicle_series_in_frame_order():
    def record(frame, ss_speed):
        return {
            "frame_number": frame,
            "real_time_seconds": frame / 30,
            "vehicles": {
                "starship": {"speed": ss_speed, "altitude": None},
                "superheavy": {"speed": None, "altitude": None},
            },
        }

    records = [record(90, 4220.0), record(0, 200.0), record(30, 205.0), record(60, 210.0)]
    stats = ds.filter_records(records)
    assert records[0]["vehicles"]["starship"]["speed"] == 220.0
    assert stats["starship.speed"] == {"readings": 4, "repaired": 1, "removed": 0}


def test_clean_series_never_rewrites_a_good_climb_to_avoid_a_garbage_cluster():
    # IFT-14 Super Heavy altitude at T+1:44..2:19: a good climb through 30, 40
    # and 50 km, then ~10 s of garbage. Rewriting the climb as 1.2, 2.0, ...
    # (dropping each decade crossing) once avoided the single break.
    times = [100 + 0.33 * k for k in range(150)]
    climb = [25.0 + 0.2 * k for k in range(130)]  # 25.0 .. 50.8
    garbage = [3.0, 21.0, 3.0, 21.0] * 5
    cleaned, repaired, _ = ds.clean_series(times, climb + garbage, ds.LIMITS["altitude"])
    assert cleaned[:130] == climb
    assert repaired == 0


def test_clean_series_drops_garbage_right_after_a_gap_in_the_data():
    # IFT-14 Starship altitude at T+7:02: hidden for minutes, then ~10 s of
    # "20.0" (engine graphic) before the real 150 km reappears
    # (the real altitude then continues for the rest of the flight)
    times = [0, 1, 2] + [300 + 0.33 * k for k in range(30)] + [312 + k for k in range(200)]
    values = [60.0, 60.5, 61.0] + [20.0] * 30 + [150.0 + 0.1 * k for k in range(200)]
    cleaned, _, dropped = ds.clean_series(times, values, ds.LIMITS["altitude"])
    assert cleaned[:3] == values[:3] and cleaned[33:] == values[33:]
    assert all(v is None for v in cleaned[3:33])
    assert dropped == 30
