"""Core data model dataclasses for the Starship Telemetry system.

Defines the ROI configuration structures produced by SVG template parsing
and the ParseError type for reporting parsing failures.
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ROIRect:
    """A rectangular Region of Interest with position and dimensions."""

    id: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ROICircle:
    """A circular Region of Interest with center coordinates and radius."""

    id: str
    cx: float
    cy: float
    r: float


@dataclass(frozen=True)
class EngineSubgroup:
    """A named subgroup of engine circles (e.g., atmo, vacuum, inner, middle, outer)."""

    name: str
    circles: list[ROICircle]


@dataclass(frozen=True)
class EngineGroup:
    """A group of engine subgroups with a computed bounding box."""

    group_id: str
    bounding_box: ROIRect
    subgroups: list[EngineSubgroup]


@dataclass(frozen=True)
class ROIConfiguration:
    """Complete ROI configuration parsed from an SVG template."""

    template_name: str
    view_box: tuple[Literal[1920], Literal[1080]]
    text_regions: dict[str, ROIRect]
    engine_groups: list[EngineGroup]


@dataclass
class ParseError:
    """Describes a failure when parsing an SVG ROI template."""

    message: str
    missing_regions: list[str] = field(default_factory=list)
    malformed_elements: list[str] = field(default_factory=list)
