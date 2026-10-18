"""Recorded telemetry of previous flights, served to the dashboard for comparison.

Each JSON file in ``results_previous/`` (e.g. ``ift-13.json``) is a ~14 MB
pretty-printed array of per-frame records. The dashboard only plots MET, the
frame clock and both vehicles' speed and altitude, so each file is reduced
once to a compact columnar payload and the encoded bytes are cached.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PREVIOUS_FLIGHTS_DIR = Path(__file__).parent.parent.parent / "results_previous"


def list_flights(directory: Path = PREVIOUS_FLIGHTS_DIR) -> list[dict[str, str]]:
    """List available flights as ``{'name': 'IFT 13', 'filename': 'ift-13'}``."""
    if not directory.exists():
        return []
    return [
        {"name": f.stem.upper().replace("-", " "), "filename": f.stem}
        for f in sorted(directory.glob("*.json"))
    ]


def _format_met(time: dict[str, Any]) -> str | None:
    """Format a record's ``time`` object as ``+HH:MM:SS`` / ``-HH:MM:SS``."""
    sign = time.get("sign")
    hours = time.get("hours")
    minutes = time.get("minutes")
    seconds = time.get("seconds")
    if sign is None or hours is None or minutes is None or seconds is None:
        return None
    prefix = "+" if sign in ("+", "T") else "-"
    return f"{prefix}{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"


def to_columns(records: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Reduce raw flight records to the columns the dashboard plots.

    Records without a complete MET are dropped. Speed and altitude columns
    keep ``None`` where a vehicle had no reading.
    """
    columns: dict[str, list[Any]] = {
        "met": [],
        "t": [],
        "ss_speed": [],
        "sh_speed": [],
        "ss_alt": [],
        "sh_alt": [],
    }
    for record in records:
        met = _format_met(record.get("time") or {})
        if met is None:
            continue
        vehicles = record.get("vehicles") or {}
        starship = vehicles.get("starship") or {}
        superheavy = vehicles.get("superheavy") or {}
        columns["met"].append(met)
        columns["t"].append(record.get("real_time_seconds"))
        columns["ss_speed"].append(starship.get("speed"))
        columns["sh_speed"].append(superheavy.get("speed"))
        columns["ss_alt"].append(starship.get("altitude"))
        columns["sh_alt"].append(superheavy.get("altitude"))
    return columns


def load_flight(name: str, directory: Path = PREVIOUS_FLIGHTS_DIR) -> bytes | None:
    """Return the columnar JSON payload for a flight, or None if not found.

    Only alphanumerics and hyphens are kept from ``name``.
    """
    safe_name = "".join(c for c in name if c.isalnum() or c == "-")
    path = directory / f"{safe_name}.json"
    if not path.is_file():
        return None
    return _encode_flight(path, path.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _encode_flight(path: Path, mtime_ns: int) -> bytes:
    """Parse, reduce and encode a flight file. Cached per (path, mtime)."""
    with open(path, "r", encoding="utf-8") as fh:
        records = json.load(fh)
    payload = json.dumps(to_columns(records), separators=(",", ":")).encode("utf-8")
    logger.info("Loaded previous flight %s: %d records", path.stem, len(records))
    return payload
