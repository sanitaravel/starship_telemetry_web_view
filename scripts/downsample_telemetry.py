"""Downsample a recorded flight's telemetry to every Nth analysed frame.

Works on the flight files in results_previous/ (a JSON array of per-frame
records with a "frame_number"). The file's own frame step is detected (e.g.
analysed every 10th source frame), and only records on every Nth step are
kept: with --factor 3 a file analysed every 10th frame becomes every 30th.
Records are selected by frame number, not position, because the files are
not stored in frame order; the output is sorted by frame.

With --filter, speed and altitude of the downsampled records are cleaned,
per vehicle: stretches misread with an extra leading digit (20.2 for 0.2,
3158 for 158) are repaired where dropping that digit makes the series
continuous, and garbage that fits neither way (e.g. the engine graphic read
as 8888) is removed (set to null). See clean_series().

Standard library only.

Usage:
    python scripts/downsample_telemetry.py results_previous/ift-13.json
    python scripts/downsample_telemetry.py results_previous/ift-13.json --factor 5
    python scripts/downsample_telemetry.py in.json --output out.json
    python scripts/downsample_telemetry.py in.json --factor 10 --filter

Without --output the result is written next to the input as
<name>.every<N>.json (e.g. ift-13.every3.json); the input is never changed.
"""

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def detect_frame_step(frame_numbers: list[int]) -> int:
    """The frame spacing the file was analysed at: the most common gap
    between consecutive distinct frame numbers (gaps from dropped frames
    are rarer and ignored)."""
    ordered = sorted(set(frame_numbers))
    gaps = Counter(b - a for a, b in zip(ordered, ordered[1:]))
    if not gaps:
        raise ValueError("need at least two distinct frame numbers to detect the frame step")
    return gaps.most_common(1)[0][0]


def downsample(records: list[dict[str, Any]], factor: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep the records on every ``factor``-th frame step, sorted by frame.

    Returns the kept records and counts describing what was done.
    """
    if factor < 1:
        raise ValueError("factor must be at least 1")
    numbered = [r for r in records if isinstance(r.get("frame_number"), int)]
    step = detect_frame_step([r["frame_number"] for r in numbered])
    stride = step * factor
    first = min(r["frame_number"] for r in numbered)

    kept: dict[int, dict[str, Any]] = {}
    for record in numbered:
        frame = record["frame_number"]
        if (frame - first) % stride == 0:
            kept.setdefault(frame, record)  # first record wins on a duplicate frame
    result = [kept[frame] for frame in sorted(kept)]
    return result, {
        "input": len(records),
        "without_frame_number": len(records) - len(numbered),
        "frame_step": step,
        "stride": stride,
        "output": len(result),
    }


# --- Filtering --------------------------------------------------------------

SERIES = (
    ("starship", "speed"),
    ("starship", "altitude"),
    ("superheavy", "speed"),
    ("superheavy", "altitude"),
)


@dataclass(frozen=True)
class Limits:
    """Plausible values for one quantity: range, and how fast it can change."""

    low: float
    high: float
    rate_per_s: float  # largest real change per second
    tolerance: float  # plus this, for display rounding between frames


LIMITS = {
    # Starship peaks around 4-5 g (~160 km/h per second); boostback and
    # landing burns stay below this
    "speed": Limits(low=0, high=30_000, rate_per_s=400, tolerance=5),
    # Climb and fall rates stay within a few km/s
    "altitude": Limits(low=0, high=300, rate_per_s=5, tolerance=0.2),
}

# Costs for the dynamic programme in clean_series(), which decides per run
# of mutually continuous readings: keep it, strip a digit from all of it, or
# drop it. A break in continuity costs as much as dropping _COST_BREAK /
# _COST_DROP readings, so short garbage runs are dropped while good data on
# both sides of a real break is kept.
_COST_BREAK = 500.0
_COST_DROP = 10.0  # per reading
_COST_REPAIR = 1.0  # per reading
# Most readings that can be dropped in one go (each one is charged
# _COST_DROP; the limit only bounds the work per run)
_MAX_SKIP_READINGS = 300
# A gap in the data longer than this always starts a new run: across a long
# gap almost any change is "continuous", which would merge garbage after the
# gap into the good run before it
_RUN_GAP_S = 10.0


def strip_leading_digit(value: float) -> float | None:
    """The reading without its first digit: 3158 -> 158, 20.2 -> 0.2.

    Older extractions misread the gauge outline next to a value as an extra
    leading digit. Returns None when nothing sensible is left (1.5 -> .5).
    """
    text = str(int(value)) if float(value).is_integer() else f"{value:g}"
    rest = text[1:]
    if not rest or rest.startswith("."):
        return None
    return float(rest)


def _continuous(a: float, b: float, dt: float, limits: Limits) -> bool:
    # The HUD updates its numbers in steps, so readings closer than a second
    # apart may differ by up to a second's worth of change
    return abs(b - a) <= limits.rate_per_s * max(abs(dt), 1.0) + limits.tolerance


def clean_series(
    times: list[float], values: list[float], limits: Limits
) -> tuple[list[float | None], int, int]:
    """Repair and filter one series of readings, in time order.

    Readings are grouped into runs whose consecutive values are continuous.
    Each run is kept as read, kept with the first digit dropped from every
    reading (only if that is continuous too), or dropped; a dynamic programme
    picks the cheapest combination (see the _COST_* constants). Deciding per
    run means a good stretch is never rewritten reading by reading to avoid a
    break, while a misread stretch is repaired as a whole. Readings out of
    range as read are taken without their first digit, or dropped if that is
    out of range too.

    Returns (cleaned values, number repaired, number dropped).
    """
    n = len(values)

    def in_range(v: float | None) -> bool:
        return v is not None and limits.low <= v <= limits.high

    # Primary value per reading (as read, or repaired if out of range) and
    # the alternative with a further digit stripped
    usable: list[int] = []
    primary: list[float] = []
    primary_repaired: list[bool] = []
    alternative: list[float | None] = []
    for i, v in enumerate(values):
        if in_range(v):
            p, repaired = v, False
        else:
            p, repaired = strip_leading_digit(v), True
            if not in_range(p):
                continue  # out of range either way
        alt = strip_leading_digit(p) if not repaired else None
        usable.append(i)
        primary.append(p)
        primary_repaired.append(repaired)
        alternative.append(alt if in_range(alt) else None)
    t = [times[i] for i in usable]
    m = len(usable)
    if m == 0:
        return [None] * n, 0, n

    def continuous(k1: int, a: float, k2: int, b: float) -> bool:
        return _continuous(a, b, t[k2] - t[k1], limits)

    # Runs of mutually continuous primary values, split at gaps: [start, end)
    runs: list[tuple[int, int]] = []
    start = 0
    for k in range(1, m + 1):
        if (
            k == m
            or t[k] - t[k - 1] > _RUN_GAP_S
            or not continuous(k - 1, primary[k - 1], k, primary[k])
        ):
            runs.append((start, k))
            start = k

    # Options per run: (values, cost, repairs)
    options: list[list[tuple[list[float], float, int]]] = []
    for lo, hi in runs:
        keep = primary[lo:hi]
        run_options = [(keep, 0.0, sum(primary_repaired[lo:hi]))]
        stripped = alternative[lo:hi]
        if all(v is not None for v in stripped) and all(
            continuous(k, stripped[k - lo - 1], k + 1, stripped[k - lo]) for k in range(lo, hi - 1)
        ):
            run_options.append((stripped, _COST_REPAIR * (hi - lo), hi - lo))
        options.append(run_options)

    # best[r][o]: cheapest series whose last kept run is r, with option o
    best: list[list[float]] = []
    back: list[list[tuple[int, int] | None]] = []
    for r, (lo, hi) in enumerate(runs):
        row_best, row_back = [], []
        for vals, own, _ in options[r]:
            # Start with this run, dropping every reading before it
            cost, origin = _COST_DROP * lo, None
            for q in range(r - 1, -1, -1):
                q_lo, q_hi = runs[q]
                if q < r - 1 and lo - q_hi > _MAX_SKIP_READINGS:
                    break
                skipped = _COST_DROP * (lo - q_hi)
                for oq, (q_vals, _, _) in enumerate(options[q]):
                    c = best[q][oq] + skipped
                    if not continuous(q_hi - 1, q_vals[-1], lo, vals[0]):
                        c += _COST_BREAK
                    if c < cost:
                        cost, origin = c, (q, oq)
            row_best.append(cost + own)
            row_back.append(origin)
        best.append(row_best)
        back.append(row_back)

    # End with the run that leaves the cheapest tail to drop
    end, total = None, float("inf")
    for r, (lo, hi) in enumerate(runs):
        for o, c in enumerate(best[r]):
            if c + _COST_DROP * (m - hi) < total:
                end, total = (r, o), c + _COST_DROP * (m - hi)

    cleaned: list[float | None] = [None] * n
    repaired = 0
    node = end
    while node is not None:
        r, o = node
        lo, hi = runs[r]
        vals, _, run_repairs = options[r][o]
        for k, value in zip(range(lo, hi), vals):
            cleaned[usable[k]] = value
        repaired += run_repairs
        node = back[r][o]
    dropped = sum(1 for v in cleaned if v is None)
    return cleaned, repaired, dropped


def _record_times(records: list[dict[str, Any]]) -> list[float]:
    """Seconds for each record (in frame order): the frame clock
    real_time_seconds when every record has one, otherwise frame_number at
    30 fps. Never mixes the two, which would put records out of order."""
    clock = [r.get("real_time_seconds") for r in records]
    if all(isinstance(t, (int, float)) for t in clock):
        return [float(t) for t in clock]
    return [r["frame_number"] / 30.0 for r in records]


def filter_records(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Repair and filter every vehicle's speed and altitude, in place.

    Records are processed in frame order; time is the frame clock
    (real_time_seconds). Returns per-series counts of readings, repairs and
    removals.
    """
    ordered = sorted(
        (r for r in records if isinstance(r.get("frame_number"), int)),
        key=lambda r: r["frame_number"],
    )
    stats: dict[str, dict[str, int]] = {}
    for vehicle, quantity in SERIES:
        readings = [
            r for r in ordered
            if (r.get("vehicles") or {}).get(vehicle, {}).get(quantity) is not None
        ]
        times = _record_times(readings)
        values = [float(r["vehicles"][vehicle][quantity]) for r in readings]
        cleaned, repaired, removed = clean_series(times, values, LIMITS[quantity])
        for record, value in zip(readings, cleaned):
            record["vehicles"][vehicle][quantity] = value
        stats[f"{vehicle}.{quantity}"] = {
            "readings": len(readings), "repaired": repaired, "removed": removed,
        }
    return stats


def default_output(path: Path, factor: int) -> Path:
    """ift-13.json -> ift-13.every3.json, next to the input."""
    return path.with_name(f"{path.stem}.every{factor}{path.suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="flight telemetry JSON (array of per-frame records)")
    parser.add_argument("--factor", type=int, default=3, help="keep every Nth analysed frame (default: 3)")
    parser.add_argument("--output", type=Path, help="output path (default: <name>.every<N>.json)")
    parser.add_argument("--filter", action="store_true",
                        help="repair extra-leading-digit misreads and remove implausible readings")
    args = parser.parse_args(argv)

    if args.factor < 1:
        parser.error("--factor must be at least 1")
    records = json.loads(args.path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        parser.error(f"{args.path} is not a JSON array of records")

    result, stats = downsample(records, args.factor)
    filter_stats = filter_records(result) if args.filter else None
    output = args.output or default_output(args.path, args.factor)
    if output.resolve() == args.path.resolve():
        parser.error("refusing to overwrite the input; pass a different --output")
    # Same layout as the source files (4-space indent)
    output.write_text(json.dumps(result, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"{args.path.name}: {stats['input']} records, analysed every {stats['frame_step']} frames"
        f" -> {stats['output']} records, every {stats['stride']} frames"
    )
    if filter_stats:
        for series, counts in filter_stats.items():
            print(
                f"  {series:20s} {counts['readings']:7d} readings: "
                f"{counts['repaired']} repaired, {counts['removed']} removed"
            )
    if stats["without_frame_number"]:
        print(f"  dropped {stats['without_frame_number']} records without a frame_number", file=sys.stderr)
    print(f"Written to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
