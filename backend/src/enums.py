"""Enums for the Starship Telemetry system.

Defines status and state enums used across the extraction pipeline
and telemetry record assembly.
"""

from enum import Enum


class EngineStatus(Enum):
    """Status of a single engine indicator detected via circle analysis."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    UNDETECTED = "undetected"


class PipelineStatus(Enum):
    """Operational state of the frame extraction pipeline."""

    STOPPED = "stopped"
    RUNNING = "running"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"


class SeparationState(Enum):
    """Stage separation state tracked as a session-level flag."""

    PRE_SEPARATION = "pre_separation"
    POST_SEPARATION = "post_separation"


class OCRFieldStatus(Enum):
    """Status of an individual OCR text field extraction attempt."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    OCCLUDED_BY_ENGINES = "occluded_by_engines"
