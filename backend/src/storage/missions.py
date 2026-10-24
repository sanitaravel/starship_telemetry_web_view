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
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


# Telemetry rules a timeline event can be confirmed by (phase B implements them).
# Mirrored in the dashboard's domain/timeline/rules.ts.
TIMELINE_RULES = (
    "liftoff",
    "sh_meco",
    "sh_boostback_start",
    "sh_boostback_end",
    "sh_landing_start",
    "sh_landing_end",
    "ss_seco",
    "ss_relight",
    "ss_landing_start",
    "ss_landing_3_to_2",
    "ss_landing_2_to_1",
    "ss_transonic",
    "ss_subsonic",
)
MAX_TIMELINE_EVENTS = 200
# Largest |t| in seconds: six hours either side of T-0
MAX_TIMELINE_SECONDS = 6 * 3600
_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


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


class _Auto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualAuto(_Auto):
    """Confirmed by a person only."""

    kind: Literal["manual"]


class ClockAuto(_Auto):
    """Happens on schedule: confirmed by the mission clock reaching its time."""

    kind: Literal["clock"]


class HudAuto(_Auto):
    """Confirmed when the broadcast HUD's milestone arc shows this label."""

    kind: Literal["hud"]
    label: str = Field(min_length=1, max_length=40)

    @field_validator("label")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("HUD label must not be blank")
        return value


class RuleAuto(_Auto):
    """Confirmed by a telemetry rule, one of ``TIMELINE_RULES``."""

    kind: Literal["rule"]
    rule: str

    @field_validator("rule")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in TIMELINE_RULES:
            raise ValueError(f"unknown timeline rule {value!r}")
        return value


TimelineAuto = Annotated[Union[ManualAuto, ClockAuto, HudAuto, RuleAuto], Field(discriminator="kind")]


class TimelineEvent(BaseModel):
    """One row of the mission timeline: a planned event at ``t`` seconds from T-0."""

    id: str
    t: int = Field(ge=-MAX_TIMELINE_SECONDS, le=MAX_TIMELINE_SECONDS)
    event: str = Field(min_length=1, max_length=200)
    vehicle: Literal["starship", "superheavy", "both"]
    auto: TimelineAuto

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _EVENT_ID.match(value):
            raise ValueError(f"invalid event id {value!r}")
        return value

    @field_validator("event")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("event text must not be blank")
        return value


class MissionSettings(BaseModel):
    """What the settings panels save for a mission: both vehicles of one flight.

    Files saved before Super Heavy had settings lack ``booster`` and
    ``superheavy_engines``; they read as empty, and so do files without a
    ``timeline``. The timeline is kept sorted by ``t``; events at the same time
    stay in the order given.
    """

    mission: str = Field(min_length=1, max_length=80)
    ship: str = Field(default="", max_length=40)
    booster: str = Field(default="", max_length=40)
    starship_engines: dict[str, str] = Field(default_factory=dict)
    superheavy_engines: dict[str, str] = Field(default_factory=dict)
    timeline: list[TimelineEvent] = Field(default_factory=list, max_length=MAX_TIMELINE_EVENTS)

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

    @field_validator("timeline")
    @classmethod
    def _sorted_unique(cls, events: list[TimelineEvent]) -> list[TimelineEvent]:
        seen: set[str] = set()
        for event in events:
            if event.id in seen:
                raise ValueError(f"duplicate timeline event id {event.id!r}")
            seen.add(event.id)
        return sorted(events, key=lambda e: e.t)  # stable: ties keep their order

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
    """Saved missions as ``{id, mission, ship, booster, updated_at}`` (no serials or timeline), newest first."""
    if not directory.exists():
        return []
    missions = []
    for path in directory.glob(f"{FILE_PREFIX}*.json"):
        record = _read(path)
        if record is not None:
            del record["starship_engines"], record["superheavy_engines"], record["timeline"]
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
