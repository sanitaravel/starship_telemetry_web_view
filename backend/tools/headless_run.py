"""Run the extraction pipeline headless against a stream and evaluate the output.

Drives the real PipelineOrchestrator (full OCR and engine analysis, no
server or GUI) for a fixed time, records every broadcast message and log
line, then checks the telemetry for throughput, timing and implausible values.

Usage (from backend/, venv active):
    python -m tools.headless_run http://localhost:8001/output.m3u8 --seconds 60

Writes to --out (default: <tmp>/starship-headless/<timestamp>/):
    run.log         every log line at DEBUG, with the structured extras
    records.jsonl   every telemetry record, with its arrival time
    frames/         frame previews named by the record sequence they follow
    summary.json    the evaluation printed at the end
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import re
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Largest plausible change between two readings, per second of MET (and the
# floor used when both readings fall in the same displayed second). Starship
# peaks around 4-5 g, i.e. ~160 km/h per second, and climbs at most a few km/s.
MAX_SPEED_RATE_KMH_PER_S = 300.0
MAX_ALTITUDE_RATE_KM_PER_S = 5.0

# The webcast clock sometimes holds a second for up to ~3 s while the video
# runs on. A reading within this of the video clock counts as a hold, beyond
# it as a misread.
MAX_CLOCK_HOLD_S = 3.5

_VEHICLES = ("super_heavy", "starship")

_EXPECTED_UNITS = {"speed": {"KM/H", "MPH"}, "altitude": {"KM", "MI", "FT"}}

# Log messages whose duration_ms / queue_ms extras are summarised
_TIMED_MESSAGES = (
    "OCR extraction completed",
    "Engine analysis completed",
    "Preview encoding completed",
    "Frame capture completed",
    "Broadcast completed",
)


# --- Capture -----------------------------------------------------------------


class _CaptureHandler(logging.Handler):
    """Keeps every log record's message, level and structured extras."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(
            {
                "t": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "duration_ms": record.__dict__.get("duration_ms"),
                "queue_ms": record.__dict__.get("queue_ms"),
            }
        )


@dataclass
class RunCapture:
    """Everything a run produced."""

    started_at: float
    ended_at: float = 0.0
    telemetry: list[dict[str, Any]] = field(default_factory=list)
    frames: int = 0
    statuses: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    # Frames the extractor handed to the pipeline, and those it discarded
    # for lack of a processing slot (both read just before stopping)
    frames_extracted: int = 0
    frames_discarded: int = 0
    # Frames still being processed when the run stopped (never broadcast)
    in_flight_at_stop: int = 0


def _configure_logging(log_path: Path) -> _CaptureHandler:
    app_logger = logging.getLogger("src")
    for handler in app_logger.handlers[:]:
        app_logger.removeHandler(handler)
    app_logger.setLevel(logging.DEBUG)
    app_logger.propagate = False

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(_ExtrasFormatter())
    app_logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    app_logger.addHandler(console)

    capture = _CaptureHandler()
    app_logger.addHandler(capture)
    return capture


class _ExtrasFormatter(logging.Formatter):
    """Plain log lines with the frame/timing extras appended."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = [
            f"{key}={record.__dict__[key]}"
            for key in ("frame_seq", "duration_ms", "queue_ms", "fps")
            if record.__dict__.get(key) is not None
        ]
        return f"{line} [{' '.join(extras)}]" if extras else line


async def run_pipeline(
    url: str, seconds: float, skip_frames: int, out_dir: Path
) -> RunCapture:
    """Run the pipeline against ``url`` for ``seconds`` and capture its output."""
    from src.gpu_detector import detect_gpu
    from src.pipeline import PipelineOrchestrator
    from src.template_registry import TemplateRegistry, load_default_template

    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    capture_handler = _configure_logging(out_dir / "run.log")
    log = logging.getLogger("src.tools.headless_run")

    registry = TemplateRegistry()
    load_default_template(registry)
    gpu = detect_gpu()
    log.info("GPU available: %s (%s)", gpu.gpu_available, gpu.device_name)

    capture = RunCapture(started_at=time.time())
    last_sequence: int | None = None
    records_file = open(out_dir / "records.jsonl", "w", encoding="utf-8")

    async def broadcast(message: dict[str, Any]) -> None:
        nonlocal last_sequence
        kind = message.get("type")
        payload = message.get("payload")
        if kind == "telemetry":
            entry = {"received_at": time.time(), **payload}
            capture.telemetry.append(entry)
            last_sequence = payload["sequence_number"]
            records_file.write(json.dumps(entry) + "\n")
        elif kind == "frame":
            capture.frames += 1
            name = f"{last_sequence:06d}.jpg" if last_sequence is not None else "pre.jpg"
            (frames_dir / name).write_bytes(base64.b64decode(payload["image"]))
        elif kind == "status":
            capture.statuses.append(payload.get("status"))
        elif kind == "error":
            capture.errors.append(payload)

    orchestrator = PipelineOrchestrator(gpu, registry, broadcast)
    # Load the OCR model before the clock starts, as the server does at startup
    orchestrator._ensure_ocr_engine()

    log.info("Running for %.0f s against %s (skip_frames=%d)", seconds, url, skip_frames)
    capture.started_at = time.time()
    await orchestrator.start(url, skip_frames)
    try:
        await asyncio.sleep(seconds)
    finally:
        capture.ended_at = time.time()
        status = orchestrator.build_status_payload()
        capture.frames_extracted = orchestrator._frame_sequence_counter
        capture.frames_discarded = status["frames_discarded"]
        capture.in_flight_at_stop = status["in_flight_frames"]
        await orchestrator.stop()
        records_file.close()
        capture.logs = capture_handler.records
    return capture


# --- Evaluation ----------------------------------------------------------------


def met_to_seconds(met: str | None) -> int | None:
    """Parse 'T+HH:MM:SS' / 'T-HH:MM:SS' (or bare HH:MM:SS) to signed seconds."""
    if not met:
        return None
    match = re.search(r"([+-])?(\d{1,2}):(\d{2}):(\d{2})$", met)
    if not match:
        return None
    sign = -1 if match.group(1) == "-" else 1
    h, m, s = (int(match.group(i)) for i in (2, 3, 4))
    return sign * (h * 3600 + m * 60 + s)


def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "mean": round(statistics.mean(ordered), 1),
        "p50": ordered[len(ordered) // 2],
        "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "max": ordered[-1],
    }


def evaluate(capture: RunCapture) -> dict[str, Any]:
    """Check a run's telemetry and logs. Returns a JSON-serialisable summary."""
    wall_s = max(capture.ended_at - capture.started_at, 1e-9)
    records = capture.telemetry
    post_t0 = [r for r in records if met_to_seconds(r.get("mission_elapsed_time")) is not None]

    summary: dict[str, Any] = {
        "wall_seconds": round(wall_s, 1),
        "records": len(records),
        "records_with_met": len(post_t0),
        "frame_previews": capture.frames,
        "records_per_second": round(len(records) / wall_s, 2),
        "statuses": dict(Counter(capture.statuses)),
        "pipeline_errors": capture.errors[:10],
    }

    # Throughput relative to the video: seconds of MET covered per wall second
    # after T-0. Below 1.0 the pipeline cannot keep up with a live stream.
    mets = [met_to_seconds(r["mission_elapsed_time"]) for r in post_t0]
    if post_t0:
        first_at = post_t0[0]["received_at"]
        last_at = post_t0[-1]["received_at"]
        met_span = mets[-1] - mets[0]
        wall_span = last_at - first_at
        summary["met_range"] = [post_t0[0]["mission_elapsed_time"], post_t0[-1]["mission_elapsed_time"]]
        summary["video_seconds_per_wall_second"] = (
            round(met_span / wall_span, 2) if wall_span > 0 else None
        )
        summary["met_backwards_steps"] = sum(1 for a, b in zip(mets, mets[1:]) if b < a)
        summary["met_gaps_over_5s"] = [
            [post_t0[i]["mission_elapsed_time"], post_t0[i + 1]["mission_elapsed_time"]]
            for i, (a, b) in enumerate(zip(mets, mets[1:]))
            if b - a > 5
        ]

    summary["dropped_frames"] = _evaluate_dropped_frames(capture)
    summary["clock"] = _evaluate_clock(records)
    summary["fields"], summary["flags"] = _evaluate_fields(post_t0)
    if summary["clock"]["misread"]:
        summary["flags"]["met_misread"] = summary["clock"]["misread"]
    summary["flags"].update(_evaluate_attribution(post_t0))
    summary["stages"] = {
        "separation_states": dict(Counter(r["separation_state"] for r in post_t0)),
        "left_labels": dict(Counter(r.get("stage_left_label") for r in post_t0)),
        "right_labels": dict(Counter(r.get("stage_right_label") for r in post_t0)),
        "assignment_left": dict(Counter(r["stage_assignment_left"] for r in post_t0)),
        "assignment_right": dict(Counter(r["stage_assignment_right"] for r in post_t0)),
    }
    summary["timings_ms"] = {
        name: {
            "duration": _stats([l["duration_ms"] for l in capture.logs
                                if l["message"] == name and l["duration_ms"] is not None]),
            "queue": _stats([l["queue_ms"] for l in capture.logs
                             if l["message"] == name and l["queue_ms"] is not None]),
        }
        for name in _TIMED_MESSAGES
    }
    fps = [float(m.group(1)) for l in capture.logs
           if (m := re.match(r"Processing FPS: ([\d.]+)", l["message"]))]
    summary["logged_processing_fps"] = _stats(fps)
    problems = Counter(
        f'{l["level"]}: {re.sub(r"[0-9]+", "N", l["message"])[:120]}'
        for l in capture.logs
        if l["level"] in ("WARNING", "ERROR", "CRITICAL")
    )
    summary["warnings_and_errors"] = dict(problems.most_common(20))
    return summary


def _evaluate_dropped_frames(capture: RunCapture) -> dict[str, Any]:
    """Account for every frame the extractor produced, and find holes in the video.

    Record timestamps are the frame's position in the video, so consecutive
    records should be one skip interval apart; a larger step means frames
    were lost anywhere between capture and broadcast.
    """
    records = capture.telemetry
    stamps = [r["timestamp"] for r in records]
    steps = [b - a for a, b in zip(stamps, stamps[1:])]
    interval = statistics.median(steps) if steps else 0
    gaps = []
    for i, step in enumerate(steps):
        if interval and step > 1.5 * interval:
            gaps.append({
                "after_seq": records[i]["sequence_number"],
                "video_s": [round(stamps[i] / 1000, 1), round(stamps[i + 1] / 1000, 1)],
                "met": [records[i]["mission_elapsed_time"], records[i + 1]["mission_elapsed_time"]],
                "frames_missing": round(step / interval) - 1,
            })

    def count(pattern: str) -> int:
        return sum(1 for l in capture.logs if re.search(pattern, l["message"]))

    unaccounted = (
        capture.frames_extracted - len(records) - capture.frames_discarded - capture.in_flight_at_stop
    )
    return {
        "frames_extracted": capture.frames_extracted,
        "records_broadcast": len(records),
        "discarded_no_slot": capture.frames_discarded,
        "in_flight_at_stop": capture.in_flight_at_stop,
        "lost_unaccounted": unaccounted,
        "record_interval_ms": interval,
        "video_gaps": gaps,
        "frames_missing_in_gaps": sum(g["frames_missing"] for g in gaps),
        "frame_timeouts": count(r"timed out|timeout"),
        "skipped_failed_frames": count(r"skipping frame|Error processing frame"),
        "stale_gaps_skipped": count(r"stale gap|Advancing past"),
        "previews_dropped": count(r"dropping frame preview"),
        "reconnects": count(r"[Rr]econnect"),
    }


def _evaluate_clock(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Check each MET reading against the video clock.

    MET minus video position is constant for a recording. A reading behind
    the median offset by more than the display's 1 s resolution, but within
    MAX_CLOCK_HOLD_S, is the webcast clock holding; anything further off was
    misread. Frames after T-0 with no readable MET are counted too.
    """
    first = next((i for i, r in enumerate(records) if r.get("mission_elapsed_time")), None)
    if first is None:
        return {"offset_s": None, "holds": [], "misread": [], "unreadable_after_t0": 0}
    after = records[first:]
    timed = [(r, met_to_seconds(r["mission_elapsed_time"]) - r["timestamp"] / 1000)
             for r in after if met_to_seconds(r.get("mission_elapsed_time")) is not None]
    offset = statistics.median(d for _, d in timed)
    def entry(r: dict[str, Any], d: float) -> dict[str, Any]:
        return {"seq": r["sequence_number"], "met": r["mission_elapsed_time"],
                "video_s": round(r["timestamp"] / 1000, 1), "off_by_s": round(d - offset, 1)}

    holds = [entry(r, d) for r, d in timed if -MAX_CLOCK_HOLD_S <= d - offset < -1.5]
    misread = [entry(r, d) for r, d in timed
               if d - offset > 1.5 or d - offset < -MAX_CLOCK_HOLD_S]
    return {
        "offset_s": round(offset, 2),
        "holds": holds,
        "misread": misread,
        "unreadable_after_t0": sum(1 for r in after if not r.get("mission_elapsed_time")),
        "unreadable_seqs": [r["sequence_number"] for r in after if not r.get("mission_elapsed_time")][:20],
    }


def _label_vehicle(label: str | None) -> str | None:
    """The vehicle a stage label names, tolerating OCR noise ("~STARSHIP")."""
    text = re.sub(r"[^A-Z]", "", (label or "").upper())
    if "SUPER" in text or "HEAVY" in text:
        return "super_heavy"
    if "STAR" in text or "SHIP" in text:
        return "starship"
    return None


def _evaluate_attribution(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Gauge readings credited to a vehicle other than the one labelled on screen.

    Only records with a readable label are checked; unlabelled ones are left
    to the stage assigner's defaults.
    """
    flags: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        for side in ("left", "right"):
            if not any(r[f"{q}_{side}"]["status"] == "available" for q in ("speed", "altitude")):
                continue
            assigned = r[f"stage_assignment_{side}"]
            where = {"seq": r["sequence_number"], "met": r["mission_elapsed_time"], "side": side,
                     "label": r[f"stage_{side}_label"], "assigned": assigned}
            if assigned not in _VEHICLES:
                flags["unknown_stage"].append(where)
                continue
            labelled = _label_vehicle(r[f"stage_{side}_label"])
            if labelled is not None and labelled != assigned:
                flags["wrong_vehicle"].append(where)
    return dict(flags)


def _evaluate_fields(records: list[dict[str, Any]]) -> tuple[dict, dict]:
    """Availability per gauge, plus flagged readings with their sequence numbers.

    Readings are compared with their neighbours on the same gauge while it
    shows the same vehicle: a spike disagrees with both neighbours (one bad
    reading), a step is a lasting jump between two consecutive readings.
    """
    fields: dict[str, Any] = {}
    flags: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for side in ("left", "right"):
        for quantity in ("speed", "altitude"):
            key = f"{quantity}_{side}"
            rate = MAX_SPEED_RATE_KMH_PER_S if quantity == "speed" else MAX_ALTITUDE_RATE_KM_PER_S
            statuses: Counter = Counter()
            units: Counter = Counter()
            # Readings split into runs that show the same vehicle
            runs: list[list[dict[str, Any]]] = []
            for r in records:
                value = r[key]
                statuses[value["status"]] += 1
                if value["status"] != "available":
                    continue
                units[value["unit"]] += 1
                v, met = value["value"], met_to_seconds(r["mission_elapsed_time"])
                where = {"field": key, "seq": r["sequence_number"], "met": r["mission_elapsed_time"],
                         "value": v, "unit": value["unit"],
                         "stage": r[f"stage_assignment_{side}"]}
                if v is None:
                    flags["available_without_value"].append(where)
                    continue
                if value["unit"] not in _EXPECTED_UNITS[quantity]:
                    flags["unexpected_unit"].append(where)
                if not runs or runs[-1][-1]["stage"] != where["stage"]:
                    runs.append([])
                runs[-1].append({**where, "met_s": met})

            def jumps(a: dict[str, Any], b: dict[str, Any]) -> bool:
                return abs(b["value"] - a["value"]) > rate * max(b["met_s"] - a["met_s"], 1)

            def public(reading: dict[str, Any]) -> dict[str, Any]:
                return {k: v for k, v in reading.items() if k != "met_s"}

            for run in runs:
                spikes = set()
                for i in range(1, len(run) - 1):
                    prev, cur, nxt = run[i - 1], run[i], run[i + 1]
                    if jumps(prev, cur) and jumps(cur, nxt) and not jumps(prev, nxt):
                        spikes.add(cur["seq"])
                        flags[f"{quantity}_spike"].append(
                            {**public(cur), "neighbours": [prev["value"], nxt["value"]]}
                        )
                for a, b in zip(run, run[1:]):
                    if jumps(a, b) and a["seq"] not in spikes and b["seq"] not in spikes:
                        flags[f"{quantity}_step"].append(
                            {**public(b), "from": a["value"], "from_met": a["met"]}
                        )
            total = sum(statuses.values()) or 1
            fields[key] = {
                "available_pct": round(100 * statuses["available"] / total, 1),
                "statuses": dict(statuses),
                "units": dict(units),
            }
    return fields, dict(flags)


def print_summary(summary: dict[str, Any]) -> None:
    print("\n=== Headless run evaluation ===")
    print(f"wall {summary['wall_seconds']} s | records {summary['records']} "
          f"({summary['records_with_met']} after T-0) | {summary['records_per_second']} rec/s")
    if "met_range" in summary:
        print(f"MET {summary['met_range'][0]} .. {summary['met_range'][1]} | "
              f"video s per wall s: {summary['video_seconds_per_wall_second']} | "
              f"MET backwards steps: {summary['met_backwards_steps']} | "
              f"gaps >5 s: {len(summary['met_gaps_over_5s'])}")
    dropped = summary["dropped_frames"]
    print("\nDropped frames:")
    print("  " + " | ".join(f"{k}={dropped[k]}" for k in (
        "frames_extracted", "records_broadcast", "discarded_no_slot", "in_flight_at_stop",
        "lost_unaccounted", "frames_missing_in_gaps")))
    print("  " + " | ".join(f"{k}={dropped[k]}" for k in (
        "frame_timeouts", "skipped_failed_frames", "stale_gaps_skipped", "previews_dropped",
        "reconnects")))
    for gap in dropped["video_gaps"][:10]:
        print(f"    gap: {gap}")
    clock = summary["clock"]
    print(f"\nClock: MET - video offset {clock['offset_s']} s | holds {len(clock['holds'])} | "
          f"misread {len(clock['misread'])} | "
          f"unreadable after T-0 {clock['unreadable_after_t0']}")
    print("\nField availability after T-0:")
    for key, info in summary["fields"].items():
        print(f"  {key:15s} {info['available_pct']:5.1f}%  statuses={info['statuses']} units={info['units']}")
    print("\nFlags:")
    if not summary["flags"]:
        print("  none")
    for name, items in summary["flags"].items():
        print(f"  {name}: {len(items)}")
        for item in items[:5]:
            print(f"    {item}")
    print("\nStages:", json.dumps(summary["stages"]))
    print("\nTimings (ms):")
    for name, t in summary["timings_ms"].items():
        if t["duration"]:
            print(f"  {name:28s} duration {t['duration']}  queue {t['queue']}")
    print("Logged processing FPS:", summary["logged_processing_fps"])
    print("\nWarnings/errors:")
    for message, count in summary["warnings_and_errors"].items():
        print(f"  {count:4d} x {message}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--skip-frames", type=int, default=30)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or (
        Path(tempfile.gettempdir()) / "starship-headless" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    capture = asyncio.run(run_pipeline(args.url, args.seconds, args.skip_frames, out_dir))
    summary = evaluate(capture)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print_summary(summary)
    print(f"\nOutput: {out_dir}")


if __name__ == "__main__":
    main()
