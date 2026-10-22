"""Per-mission engine settings, saved as one JSON file per mission.

The dashboard's engine settings panels record which Raptor serial sits in
each engine position of a mission's Starship and Super Heavy, together with
the mission, ship and booster names. Each mission is a file in ``missions/`` named
``engines_<id>.json``, where the id is the mission name as a file stem
(``IFT 14`` -> ``engines_ift-14.json``). The id matches the recorded flight's
``results_previous/ift-14.json``, but the ``engines_`` prefix and the separate
directory keep the two files apart: a recorded flight is never written to.
Missions whose serials are known are committed with the repository.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.core.paths import REPO_ROOT

logger = logging.getLogger(__name__)

MISSIONS_DIR = REPO_ROOT / "missions"

STARSHIP_ENGINES = tuple(f"ss_e{n}" for n in range(1, 7))
SUPERHEAVY_ENGINES = tuple(f"sh_e{n}" for n in range(1, 34))

# Same rule as the dashboard's normaliseSerial: upper-case letters, digits and
# dashes, at most 8 characters (e.g. "R576")
_SERIAL = re.compile(r"^[A-Z0-9-]{1,8}$")
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_ID_LENGTH = 64
FILE_PREFIX = "engines_"


def mission_id(name: str) -> str:
    """Id of a mission name: ``"IFT 14"`` -> ``"ift-14"`` (file ``engines_ift-14.json``).

    Lower case, every run of other characters becomes one hyphen. Returns ""
    when nothing usable is left.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:_MAX_ID_LENGTH].rstrip("-")


def _check_serials(engines: dict[str, str], known: tuple[str, ...], vehicle: str) -> dict[str, str]:
    for engine, serial in engines.items():
        if engine not in known:
            raise ValueError(f"unknown {vehicle} engine {engine!r}")
        if not _SERIAL.match(serial):
            raise ValueError(f"invalid serial {serial!r} for {engine}")
    return engines


class MissionSettings(BaseModel):
    """What the settings panels save for a mission: both vehicles of one flight.

    Files saved before Super Heavy had settings lack ``booster`` and
    ``superheavy_engines``; they read as empty.
    """

    mission: str = Field(min_length=1, max_length=80)
    ship: str = Field(default="", max_length=40)
    booster: str = Field(default="", max_length=40)
    starship_engines: dict[str, str] = Field(default_factory=dict)
    superheavy_engines: dict[str, str] = Field(default_factory=dict)

    @field_validator("mission", "ship", "booster")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("mission")
    @classmethod
    def _usable_name(cls, value: str) -> str:
        if not mission_id(value):
            raise ValueError("mission name needs at least one letter or digit")
        return value

    @field_validator("starship_engines")
    @classmethod
    def _valid_starship_serials(cls, engines: dict[str, str]) -> dict[str, str]:
        return _check_serials(engines, STARSHIP_ENGINES, "Starship")

    @field_validator("superheavy_engines")
    @classmethod
    def _valid_superheavy_serials(cls, engines: dict[str, str]) -> dict[str, str]:
        return _check_serials(engines, SUPERHEAVY_ENGINES, "Super Heavy")


def _path(directory: Path, identifier: str) -> Path | None:
    """The file for a mission id, or None if the id is not a valid one."""
    if len(identifier) > _MAX_ID_LENGTH or not _ID.match(identifier):
        return None
    return directory / f"{FILE_PREFIX}{identifier}.json"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        settings = MissionSettings.model_validate(data)
    except (OSError, ValueError) as exc:
        logger.warning("Skipping unreadable mission file %s: %s", path.name, exc)
        return None
    return {
        "id": path.stem.removeprefix(FILE_PREFIX),
        **settings.model_dump(),
        "updated_at": data.get("updated_at"),
    }


def list_missions(directory: Path = MISSIONS_DIR) -> list[dict[str, Any]]:
    """Saved missions as ``{id, mission, ship, booster, updated_at}``, newest first."""
    if not directory.exists():
        return []
    missions = []
    for path in directory.glob(f"{FILE_PREFIX}*.json"):
        record = _read(path)
        if record is not None:
            del record["starship_engines"], record["superheavy_engines"]
            missions.append(record)
    missions.sort(key=lambda m: m["updated_at"] or "", reverse=True)
    return missions


def load_mission(identifier: str, directory: Path = MISSIONS_DIR) -> dict[str, Any] | None:
    """One mission's saved settings, or None if there is no such mission."""
    path = _path(directory, identifier)
    if path is None or not path.is_file():
        return None
    return _read(path)


def save_mission(settings: MissionSettings, directory: Path = MISSIONS_DIR) -> dict[str, Any]:
    """Write a mission's settings to its own file and return what was saved.

    Written to a temporary file first and then moved into place, so a crash
    mid-write never leaves a truncated mission behind.
    """
    identifier = mission_id(settings.mission)
    path = _path(directory, identifier)
    assert path is not None  # mission_id only yields valid ids
    record = {
        **settings.model_dump(),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(temporary, path)
    logger.info("Saved mission settings %s", path.name)
    return {"id": identifier, **record}
