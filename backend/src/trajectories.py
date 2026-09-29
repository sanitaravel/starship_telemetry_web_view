"""Historical ship trajectories recorded from the SpaceX Starship tracker feed.

Each CSV in ``trajectories/`` (e.g. ``positions_ship39.csv``) is a log of polled
``current`` samples with the columns ``polled_at, ship, gps_time, mission_time,
latitude, longitude, altitude, speed``. Polls often repeat the same sample, and
the ends of a flight include below-ground readings; both are dropped on load.
"""

import csv
import logging
import math
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRAJECTORIES_DIR = Path(__file__).parent.parent.parent / "trajectories"

_FILE_PATTERN = re.compile(r"^(?:positions_)?(ship(\d+))$")
_NAME_PATTERN = re.compile(r"^ship\d+$")


def _ship_id(path: Path) -> tuple[str, int] | None:
    """Return ``('ship39', 39)`` for ``positions_ship39.csv``, else None."""
    match = _FILE_PATTERN.match(path.stem.lower())
    if not match:
        return None
    return match.group(1), int(match.group(2))


def list_trajectories(directory: Path = TRAJECTORIES_DIR) -> list[dict[str, Any]]:
    """List available trajectories, ordered by ship number."""
    if not directory.is_dir():
        return []
    entries = []
    for path in directory.glob("*.csv"):
        ident = _ship_id(path)
        if ident is None:
            continue
        name, number = ident
        entries.append({"name": name, "number": number, "label": f"Ship {number}"})
    return sorted(entries, key=lambda e: e["number"])


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def load_trajectory(name: str, directory: Path = TRAJECTORIES_DIR) -> list[dict[str, float]] | None:
    """Load one ship's trajectory as points ordered by GPS time.

    Rows with altitude below 0, missing coordinates, or a gps_time not later
    than the previous kept row are skipped. Returns None if no file matches.
    """
    safe_name = name.lower()
    if not _NAME_PATTERN.match(safe_name):
        return None
    candidates = (directory / f"positions_{safe_name}.csv", directory / f"{safe_name}.csv")
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return None

    points: list[dict[str, float]] = []
    last_gps_time = -math.inf
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            gps_time = _to_float(row.get("gps_time"))
            latitude = _to_float(row.get("latitude"))
            longitude = _to_float(row.get("longitude"))
            altitude = _to_float(row.get("altitude"))
            if gps_time is None or latitude is None or longitude is None or altitude is None:
                continue
            if altitude < 0 or gps_time <= last_gps_time:
                continue
            last_gps_time = gps_time
            points.append({
                "gps_time": gps_time,
                "mission_time": _to_float(row.get("mission_time")) or 0.0,
                "latitude": latitude,
                "longitude": longitude,
                "altitude": altitude,
            })
    logger.debug("Loaded trajectory %s: %d points", safe_name, len(points))
    return points
