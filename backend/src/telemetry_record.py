"""Telemetry Record model with Pydantic validation and serialization.

Defines the TelemetryRecord Pydantic model for structured telemetry data
from a single video frame, along with serialization/deserialization utilities.
"""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError


class TelemetryFieldValue(BaseModel):
    """A telemetry field with value, unit, and extraction status."""

    value: float | None = None
    unit: str | None = None
    status: Literal["available", "unavailable", "occluded_by_engines"]


class DetectionAccuracy(BaseModel):
    """Detection accuracy metrics for engine groups."""

    starship: float
    superheavy: float


class TelemetryRecord(BaseModel):
    """Structured telemetry data extracted from a single video frame.

    Contains all OCR-extracted values, engine statuses, stage assignments,
    and metadata for a single frame in the extraction pipeline.
    """

    sequence_number: int
    mission_elapsed_time: str | None = None
    mission_elapsed_time_raw: str | None = None
    speed_left: TelemetryFieldValue
    speed_right: TelemetryFieldValue
    altitude_left: TelemetryFieldValue
    altitude_right: TelemetryFieldValue
    stage_left_label: str | None = None
    stage_right_label: str | None = None
    stage_separation_text: str | None = None
    stage_assignment_left: str
    stage_assignment_right: str
    separation_state: Literal["pre_separation", "post_separation"]
    t_zero_found: bool = False
    stage_sep_found: bool = False
    starship_engines: dict[str, Literal["active", "inactive", "undetected"]]
    superheavy_engines: dict[str, Literal["active", "inactive", "undetected"]]
    detection_accuracy: DetectionAccuracy
    timestamp: int


class ValidationError(BaseModel):
    """Describes a validation failure when deserializing a TelemetryRecord."""

    message: str
    missing_fields: list[str] = Field(default_factory=list)
    type_errors: list[str] = Field(default_factory=list)


def serialize_telemetry_record(record: TelemetryRecord) -> str:
    """Serialize a TelemetryRecord to a JSON string via Pydantic.

    Args:
        record: A valid TelemetryRecord instance.

    Returns:
        JSON string representation of the record.
    """
    return record.model_dump_json()


def deserialize_telemetry_record(json_str: str) -> TelemetryRecord | ValidationError:
    """Deserialize a JSON string into a TelemetryRecord.

    Attempts to parse and validate the JSON payload. If the payload is missing
    required fields or contains type errors, returns a ValidationError with
    descriptive information about the failures.

    Args:
        json_str: JSON string to deserialize.

    Returns:
        A valid TelemetryRecord on success, or a ValidationError on failure.
    """
    try:
        return TelemetryRecord.model_validate_json(json_str)
    except PydanticValidationError as e:
        missing_fields: list[str] = []
        type_errors: list[str] = []

        for error in e.errors():
            field_path = ".".join(str(loc) for loc in error["loc"])
            error_type = error["type"]

            if error_type == "missing":
                missing_fields.append(field_path)
            else:
                type_errors.append(f"{field_path}: {error['msg']}")

        message_parts: list[str] = []
        if missing_fields:
            message_parts.append(
                f"Missing required fields: {', '.join(missing_fields)}"
            )
        if type_errors:
            message_parts.append(
                f"Type errors: {'; '.join(type_errors)}"
            )

        message = ". ".join(message_parts) if message_parts else str(e)

        return ValidationError(
            message=message,
            missing_fields=missing_fields,
            type_errors=type_errors,
        )
    except json.JSONDecodeError as e:
        return ValidationError(
            message=f"Invalid JSON: {e}",
            missing_fields=[],
            type_errors=[],
        )
