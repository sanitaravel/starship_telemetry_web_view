"""Tests for the TelemetryRecord model serialization and deserialization."""

import json

import pytest

from src.telemetry_record import (
    DetectionAccuracy,
    TelemetryFieldValue,
    TelemetryRecord,
    ValidationError,
    deserialize_telemetry_record,
    serialize_telemetry_record,
)


def _make_sample_record() -> TelemetryRecord:
    """Create a sample TelemetryRecord for testing."""
    return TelemetryRecord(
        sequence_number=142,
        mission_elapsed_time="T+00:02:35",
        speed_left=TelemetryFieldValue(value=1523.0, unit="KM/H", status="available"),
        speed_right=TelemetryFieldValue(value=2100.5, unit="KM/H", status="available"),
        altitude_left=TelemetryFieldValue(value=48.2, unit="KM", status="available"),
        altitude_right=TelemetryFieldValue(value=72.0, unit="KM", status="available"),
        stage_left_label="SUPER HEAVY",
        stage_right_label="STARSHIP",
        stage_separation_text="STAGE SEP",
        stage_assignment_left="super_heavy",
        stage_assignment_right="starship",
        separation_state="post_separation",
        starship_engines={
            "e1": "active",
            "e2": "active",
            "e3": "inactive",
            "e4": "active",
            "e5": "active",
            "e6": "undetected",
        },
        superheavy_engines={f"e{i}": "inactive" for i in range(1, 34)},
        detection_accuracy=DetectionAccuracy(starship=0.83, superheavy=0.97),
        timestamp=1700000000000,
    )


class TestTelemetryRecordCreation:
    """Test TelemetryRecord model creation and validation."""

    def test_create_valid_record(self):
        record = _make_sample_record()
        assert record.sequence_number == 142
        assert record.mission_elapsed_time == "T+00:02:35"
        assert record.speed_left.value == 1523.0
        assert record.speed_left.unit == "KM/H"
        assert record.speed_left.status == "available"
        assert record.separation_state == "post_separation"
        assert len(record.starship_engines) == 6
        assert len(record.superheavy_engines) == 33
        assert record.detection_accuracy.starship == 0.83

    def test_nullable_fields(self):
        record = TelemetryRecord(
            sequence_number=1,
            mission_elapsed_time=None,
            speed_left=TelemetryFieldValue(value=None, unit=None, status="unavailable"),
            speed_right=TelemetryFieldValue(value=None, unit=None, status="unavailable"),
            altitude_left=TelemetryFieldValue(value=None, unit=None, status="occluded_by_engines"),
            altitude_right=TelemetryFieldValue(value=None, unit=None, status="unavailable"),
            stage_left_label=None,
            stage_right_label=None,
            stage_separation_text=None,
            stage_assignment_left="super_heavy",
            stage_assignment_right="super_heavy",
            separation_state="pre_separation",
            starship_engines={f"e{i}": "undetected" for i in range(1, 7)},
            superheavy_engines={f"e{i}": "undetected" for i in range(1, 34)},
            detection_accuracy=DetectionAccuracy(starship=0.0, superheavy=0.0),
            timestamp=1700000000000,
        )
        assert record.mission_elapsed_time is None
        assert record.speed_left.value is None
        assert record.altitude_left.status == "occluded_by_engines"


class TestSerialization:
    """Test serialize_telemetry_record function."""

    def test_serialize_produces_valid_json(self):
        record = _make_sample_record()
        json_str = serialize_telemetry_record(record)
        data = json.loads(json_str)
        assert data["sequence_number"] == 142
        assert data["mission_elapsed_time"] == "T+00:02:35"
        assert data["speed_left"]["value"] == 1523.0
        assert data["speed_left"]["unit"] == "KM/H"
        assert data["speed_left"]["status"] == "available"
        assert data["separation_state"] == "post_separation"
        assert data["starship_engines"]["e1"] == "active"
        assert data["detection_accuracy"]["starship"] == 0.83

    def test_serialize_null_fields(self):
        record = TelemetryRecord(
            sequence_number=1,
            mission_elapsed_time=None,
            speed_left=TelemetryFieldValue(value=None, unit=None, status="unavailable"),
            speed_right=TelemetryFieldValue(value=None, unit=None, status="unavailable"),
            altitude_left=TelemetryFieldValue(value=None, unit=None, status="unavailable"),
            altitude_right=TelemetryFieldValue(value=None, unit=None, status="unavailable"),
            stage_left_label=None,
            stage_right_label=None,
            stage_separation_text=None,
            stage_assignment_left="super_heavy",
            stage_assignment_right="super_heavy",
            separation_state="pre_separation",
            starship_engines={f"e{i}": "undetected" for i in range(1, 7)},
            superheavy_engines={f"e{i}": "undetected" for i in range(1, 34)},
            detection_accuracy=DetectionAccuracy(starship=0.0, superheavy=0.0),
            timestamp=1700000000000,
        )
        json_str = serialize_telemetry_record(record)
        data = json.loads(json_str)
        assert data["mission_elapsed_time"] is None
        assert data["speed_left"]["value"] is None


class TestDeserialization:
    """Test deserialize_telemetry_record function."""

    def test_round_trip(self):
        """Serializing then deserializing produces equivalent record (Req 9.3)."""
        record = _make_sample_record()
        json_str = serialize_telemetry_record(record)
        result = deserialize_telemetry_record(json_str)
        assert isinstance(result, TelemetryRecord)
        assert result == record

    def test_missing_required_fields_returns_validation_error(self):
        """Missing required fields return a descriptive ValidationError (Req 9.4)."""
        bad_json = json.dumps({"sequence_number": 1})
        result = deserialize_telemetry_record(bad_json)
        assert isinstance(result, ValidationError)
        assert len(result.missing_fields) > 0
        assert "speed_left" in result.missing_fields
        assert "timestamp" in result.missing_fields

    def test_type_error_returns_validation_error(self):
        """Type errors produce a ValidationError with type_errors list."""
        data = {
            "sequence_number": "not_a_number",
            "mission_elapsed_time": None,
            "speed_left": {"value": None, "unit": None, "status": "available"},
            "speed_right": {"value": None, "unit": None, "status": "available"},
            "altitude_left": {"value": None, "unit": None, "status": "available"},
            "altitude_right": {"value": None, "unit": None, "status": "available"},
            "stage_assignment_left": "super_heavy",
            "stage_assignment_right": "super_heavy",
            "separation_state": "pre_separation",
            "starship_engines": {},
            "superheavy_engines": {},
            "detection_accuracy": {"starship": 0.5, "superheavy": 0.5},
            "timestamp": 123,
        }
        result = deserialize_telemetry_record(json.dumps(data))
        assert isinstance(result, ValidationError)
        assert len(result.type_errors) > 0

    def test_invalid_json_returns_validation_error(self):
        """Completely invalid JSON returns a ValidationError."""
        result = deserialize_telemetry_record("not json at all")
        assert isinstance(result, ValidationError)
        assert "Invalid JSON" in result.message

    def test_invalid_enum_value_returns_validation_error(self):
        """Invalid literal values return a ValidationError."""
        data = {
            "sequence_number": 1,
            "mission_elapsed_time": None,
            "speed_left": {"value": None, "unit": None, "status": "bogus_status"},
            "speed_right": {"value": None, "unit": None, "status": "available"},
            "altitude_left": {"value": None, "unit": None, "status": "available"},
            "altitude_right": {"value": None, "unit": None, "status": "available"},
            "stage_assignment_left": "super_heavy",
            "stage_assignment_right": "super_heavy",
            "separation_state": "pre_separation",
            "starship_engines": {},
            "superheavy_engines": {},
            "detection_accuracy": {"starship": 0.5, "superheavy": 0.5},
            "timestamp": 123,
        }
        result = deserialize_telemetry_record(json.dumps(data))
        assert isinstance(result, ValidationError)
        assert len(result.type_errors) > 0

    def test_empty_json_object_returns_validation_error(self):
        """An empty JSON object should return ValidationError with missing fields."""
        result = deserialize_telemetry_record("{}")
        assert isinstance(result, ValidationError)
        assert len(result.missing_fields) > 0


# --- Property-Based Tests (Hypothesis) ---

from hypothesis import given, settings
from hypothesis.strategies import (
    booleans,
    composite,
    dictionaries,
    floats,
    from_type,
    integers,
    just,
    lists,
    none,
    one_of,
    sampled_from,
    sets,
    text,
)


# Hypothesis strategy for generating valid TelemetryFieldValue objects
@composite
def telemetry_field_values(draw):
    status = draw(sampled_from(["available", "unavailable", "occluded_by_engines"]))
    if status == "available":
        value = draw(one_of(none(), floats(allow_nan=False, allow_infinity=False)))
        unit = draw(one_of(none(), sampled_from(["KM/H", "M/S", "KM", "M", "MPH"])))
    else:
        value = draw(one_of(none(), floats(allow_nan=False, allow_infinity=False)))
        unit = draw(one_of(none(), sampled_from(["KM/H", "M/S", "KM", "M", "MPH"])))
    return TelemetryFieldValue(value=value, unit=unit, status=status)


# Hypothesis strategy for generating valid DetectionAccuracy objects
@composite
def detection_accuracies(draw):
    starship = draw(floats(min_value=0.0, max_value=1.0, allow_nan=False))
    superheavy = draw(floats(min_value=0.0, max_value=1.0, allow_nan=False))
    return DetectionAccuracy(starship=starship, superheavy=superheavy)


# Hypothesis strategy for generating valid TelemetryRecord objects
@composite
def telemetry_records(draw):
    """Generate arbitrary valid TelemetryRecord objects with realistic constraints."""
    engine_statuses = sampled_from(["active", "inactive", "undetected"])

    # Exactly 6 starship engines keyed "e1"-"e6"
    starship_engines = {f"e{i}": draw(engine_statuses) for i in range(1, 7)}

    # Exactly 33 superheavy engines keyed "e1"-"e33"
    superheavy_engines = {f"e{i}": draw(engine_statuses) for i in range(1, 34)}

    return TelemetryRecord(
        sequence_number=draw(integers(min_value=0, max_value=2**31 - 1)),
        mission_elapsed_time=draw(
            one_of(none(), sampled_from(["T+00:00:00", "T+01:23:45", "T-00:00:10", "T+12:59:59"]))
        ),
        speed_left=draw(telemetry_field_values()),
        speed_right=draw(telemetry_field_values()),
        altitude_left=draw(telemetry_field_values()),
        altitude_right=draw(telemetry_field_values()),
        stage_left_label=draw(one_of(none(), text(min_size=1, max_size=30))),
        stage_right_label=draw(one_of(none(), text(min_size=1, max_size=30))),
        stage_separation_text=draw(one_of(none(), text(min_size=1, max_size=30))),
        stage_assignment_left=draw(sampled_from(["super_heavy", "starship"])),
        stage_assignment_right=draw(sampled_from(["super_heavy", "starship"])),
        separation_state=draw(sampled_from(["pre_separation", "post_separation"])),
        starship_engines=starship_engines,
        superheavy_engines=superheavy_engines,
        detection_accuracy=draw(detection_accuracies()),
        timestamp=draw(integers(min_value=0, max_value=2**53 - 1)),
    )


class TestPropertySerializationRoundTrip:
    """Property-based tests for TelemetryRecord serialization.

    **Validates: Requirements 9.3, 9.4**
    """

    @given(record=telemetry_records())
    @settings(max_examples=100)
    def test_serialization_round_trip(self, record: TelemetryRecord):
        """Property 15: Telemetry Record Serialization Round-Trip.

        For any valid TelemetryRecord object, serializing to JSON and then
        deserializing SHALL produce an equivalent TelemetryRecord object
        with all fields preserved.

        **Validates: Requirements 9.3**
        """
        json_str = serialize_telemetry_record(record)
        result = deserialize_telemetry_record(json_str)
        assert isinstance(result, TelemetryRecord), (
            f"Expected TelemetryRecord but got ValidationError: {result}"
        )
        assert result == record

    @given(
        fields_to_remove=sets(
            sampled_from([
                "sequence_number",
                "speed_left",
                "speed_right",
                "altitude_left",
                "altitude_right",
                "stage_assignment_left",
                "stage_assignment_right",
                "separation_state",
                "starship_engines",
                "superheavy_engines",
                "detection_accuracy",
                "timestamp",
            ]),
            min_size=1,
        )
    )
    @settings(max_examples=100)
    def test_validation_error_on_missing_fields(self, fields_to_remove: set):
        """Property 16: Validation Error on Missing Fields.

        For any JSON payload that is missing one or more required TelemetryRecord
        fields, deserialization SHALL return a ValidationError that identifies
        the missing field names.

        **Validates: Requirements 9.4**
        """
        # Start from a complete valid record and remove fields
        record = _make_sample_record()
        json_str = serialize_telemetry_record(record)
        data = json.loads(json_str)

        # Remove the selected fields
        for field_name in fields_to_remove:
            data.pop(field_name, None)

        # Deserialize the incomplete payload
        result = deserialize_telemetry_record(json.dumps(data))
        assert isinstance(result, ValidationError), (
            f"Expected ValidationError but got TelemetryRecord for missing fields: {fields_to_remove}"
        )

        # All removed required fields should be reported as missing
        for field_name in fields_to_remove:
            assert field_name in result.missing_fields, (
                f"Field '{field_name}' was removed but not reported in missing_fields. "
                f"Reported: {result.missing_fields}"
            )
