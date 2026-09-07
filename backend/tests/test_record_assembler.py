"""Unit tests for the Record Assembler module."""

import pytest

from src.engine_analyzer import EngineAnalysisResult
from src.enums import EngineStatus, OCRFieldStatus, SeparationState
from src.models import EngineGroup, EngineSubgroup, ROICircle, ROIRect
from src.ocr_engine import OCRFieldResult, OCRResult
from src.record_assembler import (
    RecordAssembler,
    _compute_detection_accuracy,
    _extract_field_text,
    _ocr_field_to_telemetry_field,
    _split_engine_statuses,
)
from src.stage_assignment import StageAssignmentResult


# Frame-based timing constants used across assemble() calls in these tests.
# 1200 frames at 30 fps => 40_000 ms video-stream offset.
_TEST_FRAME_NUMBER = 1200
_TEST_SOURCE_FPS = 30.0


# --- Fixtures ---


def _make_ocr_field(
    status: OCRFieldStatus = OCRFieldStatus.AVAILABLE,
    raw_text: str | None = None,
    parsed_value: float | str | None = None,
) -> OCRFieldResult:
    return OCRFieldResult(status=status, raw_text=raw_text, parsed_value=parsed_value)


def _make_starship_engine_group() -> EngineGroup:
    """Create a starship engine group with 6 engines."""
    circles = [ROICircle(id=f"e{i}", cx=float(i * 10), cy=20.0, r=5.0) for i in range(1, 7)]
    subgroup = EngineSubgroup(name="all", circles=circles)
    bbox = ROIRect(id="starship_bbox", x=0, y=0, width=100, height=100)
    return EngineGroup(group_id="engines_starship", bounding_box=bbox, subgroups=[subgroup])


def _make_superheavy_engine_group() -> EngineGroup:
    """Create a superheavy engine group with 33 engines."""
    circles = [ROICircle(id=f"sh{i}", cx=float(i * 3), cy=50.0, r=5.0) for i in range(1, 34)]
    subgroup = EngineSubgroup(name="all", circles=circles)
    bbox = ROIRect(id="superheavy_bbox", x=0, y=100, width=100, height=100)
    return EngineGroup(group_id="engines_superheavy", bounding_box=bbox, subgroups=[subgroup])


def _make_engine_groups() -> list[EngineGroup]:
    return [_make_starship_engine_group(), _make_superheavy_engine_group()]


def _make_engine_result(
    starship_status: EngineStatus = EngineStatus.ACTIVE,
    superheavy_status: EngineStatus = EngineStatus.INACTIVE,
) -> EngineAnalysisResult:
    """Create an EngineAnalysisResult with uniform statuses per group."""
    statuses: dict[str, EngineStatus] = {}
    for i in range(1, 7):
        statuses[f"e{i}"] = starship_status
    for i in range(1, 34):
        statuses[f"sh{i}"] = superheavy_status
    return EngineAnalysisResult(
        engine_statuses=statuses,
        detection_accuracy=1.0,
        engine_group_bounding_boxes=[],
    )


def _make_ocr_result(
    time_val: str = "T+00:01:30",
    speed_l: float = 1200.0,
    speed_r: float = 1500.0,
    altitude_l: float = 45.0,
    altitude_r: float = 60.0,
    speed_unit: str = "KM/H",
    altitude_unit: str = "KM",
) -> OCRResult:
    """Create a fully available OCRResult."""
    return OCRResult(
        time=_make_ocr_field(OCRFieldStatus.AVAILABLE, time_val, time_val),
        speed_l=_make_ocr_field(OCRFieldStatus.AVAILABLE, str(speed_l), speed_l),
        speed_l_unit=_make_ocr_field(OCRFieldStatus.AVAILABLE, speed_unit, speed_unit),
        speed_r=_make_ocr_field(OCRFieldStatus.AVAILABLE, str(speed_r), speed_r),
        speed_r_unit=_make_ocr_field(OCRFieldStatus.AVAILABLE, speed_unit, speed_unit),
        altitude_l=_make_ocr_field(OCRFieldStatus.AVAILABLE, str(altitude_l), altitude_l),
        altitude_l_unit=_make_ocr_field(OCRFieldStatus.AVAILABLE, altitude_unit, altitude_unit),
        altitude_r=_make_ocr_field(OCRFieldStatus.AVAILABLE, str(altitude_r), altitude_r),
        altitude_r_unit=_make_ocr_field(OCRFieldStatus.AVAILABLE, altitude_unit, altitude_unit),
        stage_l=_make_ocr_field(OCRFieldStatus.AVAILABLE, "SUPER HEAVY", "SUPER HEAVY"),
        stage_r=_make_ocr_field(OCRFieldStatus.AVAILABLE, "STARSHIP", "STARSHIP"),
        stage_sep_text=_make_ocr_field(OCRFieldStatus.AVAILABLE, "STAGE SEP", "STAGE SEP"),
    )


def _make_stage_result(
    left: str = "super_heavy",
    right: str = "starship",
    state: SeparationState = SeparationState.POST_SEPARATION,
) -> StageAssignmentResult:
    return StageAssignmentResult(left_stage=left, right_stage=right, separation_state=state)


# --- Tests for _ocr_field_to_telemetry_field ---


class TestOCRFieldToTelemetryField:
    def test_available_field_with_unit(self):
        value_field = _make_ocr_field(OCRFieldStatus.AVAILABLE, "1200", 1200.0)
        unit_field = _make_ocr_field(OCRFieldStatus.AVAILABLE, "KM/H", "KM/H")
        result = _ocr_field_to_telemetry_field(value_field, unit_field)
        assert result.value == 1200.0
        assert result.unit == "KM/H"
        assert result.status == "available"

    def test_unavailable_field(self):
        value_field = _make_ocr_field(OCRFieldStatus.UNAVAILABLE)
        unit_field = _make_ocr_field(OCRFieldStatus.UNAVAILABLE)
        result = _ocr_field_to_telemetry_field(value_field, unit_field)
        assert result.value is None
        assert result.unit is None
        assert result.status == "unavailable"

    def test_occluded_field(self):
        value_field = _make_ocr_field(OCRFieldStatus.OCCLUDED_BY_ENGINES)
        unit_field = _make_ocr_field(OCRFieldStatus.OCCLUDED_BY_ENGINES)
        result = _ocr_field_to_telemetry_field(value_field, unit_field)
        assert result.value is None
        assert result.unit is None
        assert result.status == "occluded_by_engines"

    def test_value_available_unit_unavailable(self):
        value_field = _make_ocr_field(OCRFieldStatus.AVAILABLE, "500", 500.0)
        unit_field = _make_ocr_field(OCRFieldStatus.UNAVAILABLE)
        result = _ocr_field_to_telemetry_field(value_field, unit_field)
        assert result.value == 500.0
        assert result.unit is None
        assert result.status == "available"


# --- Tests for _extract_field_text ---


class TestExtractFieldText:
    def test_available_with_parsed_value(self):
        field = _make_ocr_field(OCRFieldStatus.AVAILABLE, "raw", "parsed")
        assert _extract_field_text(field) == "parsed"

    def test_available_with_only_raw_text(self):
        field = _make_ocr_field(OCRFieldStatus.AVAILABLE, "raw", None)
        assert _extract_field_text(field) == "raw"

    def test_unavailable(self):
        field = _make_ocr_field(OCRFieldStatus.UNAVAILABLE)
        assert _extract_field_text(field) is None


# --- Tests for _split_engine_statuses ---


class TestSplitEngineStatuses:
    def test_splits_correctly_with_groups(self):
        groups = _make_engine_groups()
        statuses: dict[str, EngineStatus] = {}
        for i in range(1, 7):
            statuses[f"e{i}"] = EngineStatus.ACTIVE
        for i in range(1, 34):
            statuses[f"sh{i}"] = EngineStatus.INACTIVE

        starship, superheavy = _split_engine_statuses(statuses, groups)

        assert len(starship) == 6
        assert len(superheavy) == 33
        assert all(v == "active" for v in starship.values())
        assert all(v == "inactive" for v in superheavy.values())

    def test_empty_statuses(self):
        groups = _make_engine_groups()
        starship, superheavy = _split_engine_statuses({}, groups)
        assert starship == {}
        assert superheavy == {}


# --- Tests for _compute_detection_accuracy ---


class TestComputeDetectionAccuracy:
    def test_all_detected(self):
        groups = _make_engine_groups()
        statuses: dict[str, EngineStatus] = {}
        for i in range(1, 7):
            statuses[f"e{i}"] = EngineStatus.ACTIVE
        for i in range(1, 34):
            statuses[f"sh{i}"] = EngineStatus.INACTIVE

        acc = _compute_detection_accuracy(statuses, groups)
        assert acc.starship == 1.0
        assert acc.superheavy == 1.0

    def test_none_detected(self):
        groups = _make_engine_groups()
        statuses: dict[str, EngineStatus] = {}
        for i in range(1, 7):
            statuses[f"e{i}"] = EngineStatus.UNDETECTED
        for i in range(1, 34):
            statuses[f"sh{i}"] = EngineStatus.UNDETECTED

        acc = _compute_detection_accuracy(statuses, groups)
        assert acc.starship == 0.0
        assert acc.superheavy == 0.0

    def test_partial_detection(self):
        groups = _make_engine_groups()
        statuses: dict[str, EngineStatus] = {}
        # 3 out of 6 starship detected
        for i in range(1, 4):
            statuses[f"e{i}"] = EngineStatus.ACTIVE
        for i in range(4, 7):
            statuses[f"e{i}"] = EngineStatus.UNDETECTED
        # All superheavy detected
        for i in range(1, 34):
            statuses[f"sh{i}"] = EngineStatus.INACTIVE

        acc = _compute_detection_accuracy(statuses, groups)
        assert acc.starship == 0.5
        assert acc.superheavy == 1.0


# --- Tests for RecordAssembler ---


class TestRecordAssembler:
    def test_assemble_produces_valid_record(self):
        assembler = RecordAssembler()
        engine_result = _make_engine_result()
        ocr_result = _make_ocr_result()
        stage_result = _make_stage_result()
        groups = _make_engine_groups()

        record = assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)

        assert record.sequence_number == 1
        assert record.mission_elapsed_time == "T+00:01:30"
        assert record.speed_left.value == 1200.0
        assert record.speed_left.unit == "KM/H"
        assert record.speed_left.status == "available"
        assert record.speed_right.value == 1500.0
        assert record.altitude_left.value == 45.0
        assert record.altitude_right.value == 60.0
        assert record.stage_left_label == "SUPER HEAVY"
        assert record.stage_right_label == "STARSHIP"
        assert record.stage_separation_text == "STAGE SEP"
        assert record.stage_assignment_left == "super_heavy"
        assert record.stage_assignment_right == "starship"
        assert record.separation_state == "post_separation"
        assert len(record.starship_engines) == 6
        assert len(record.superheavy_engines) == 33
        assert all(v == "active" for v in record.starship_engines.values())
        assert all(v == "inactive" for v in record.superheavy_engines.values())
        assert record.detection_accuracy.starship == 1.0
        assert record.detection_accuracy.superheavy == 1.0
        assert record.timestamp > 0

    def test_sequence_number_monotonically_increases(self):
        assembler = RecordAssembler()
        engine_result = _make_engine_result()
        ocr_result = _make_ocr_result()
        stage_result = _make_stage_result()
        groups = _make_engine_groups()

        r1 = assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)
        r2 = assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)
        r3 = assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)

        assert r1.sequence_number == 1
        assert r2.sequence_number == 2
        assert r3.sequence_number == 3

    def test_reset_resets_sequence_counter(self):
        assembler = RecordAssembler()
        engine_result = _make_engine_result()
        ocr_result = _make_ocr_result()
        stage_result = _make_stage_result()
        groups = _make_engine_groups()

        assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)
        assembler.reset()

        record = assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)
        assert record.sequence_number == 1

    def test_current_sequence_property(self):
        assembler = RecordAssembler()
        assert assembler.current_sequence == 0

        engine_result = _make_engine_result()
        ocr_result = _make_ocr_result()
        stage_result = _make_stage_result()
        groups = _make_engine_groups()

        assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)
        assert assembler.current_sequence == 1

    def test_unavailable_ocr_fields(self):
        assembler = RecordAssembler()
        engine_result = _make_engine_result()
        groups = _make_engine_groups()

        # All OCR fields unavailable
        unavailable = _make_ocr_field(OCRFieldStatus.UNAVAILABLE)
        ocr_result = OCRResult(
            time=unavailable,
            speed_l=unavailable,
            speed_l_unit=unavailable,
            speed_r=unavailable,
            speed_r_unit=unavailable,
            altitude_l=unavailable,
            altitude_l_unit=unavailable,
            altitude_r=unavailable,
            altitude_r_unit=unavailable,
            stage_l=unavailable,
            stage_r=unavailable,
            stage_sep_text=unavailable,
        )
        stage_result = _make_stage_result(
            "super_heavy", "super_heavy", SeparationState.PRE_SEPARATION
        )

        record = assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)

        assert record.mission_elapsed_time is None
        assert record.speed_left.value is None
        assert record.speed_left.unit is None
        assert record.speed_left.status == "unavailable"
        assert record.stage_left_label is None
        assert record.stage_separation_text is None
        assert record.separation_state == "pre_separation"

    def test_timestamp_is_frame_based(self):
        """Timestamp is derived from the frame's position in the video stream."""
        assembler = RecordAssembler()
        engine_result = _make_engine_result()
        ocr_result = _make_ocr_result()
        stage_result = _make_stage_result()
        groups = _make_engine_groups()

        # frame 900 at 30 fps => 30_000 ms into the stream.
        record = assembler.assemble(
            engine_result, ocr_result, stage_result, groups, 900, 30.0
        )

        assert record.timestamp == 30_000

    def test_timestamp_scales_with_frame_number(self):
        """A later frame yields a strictly larger timestamp at the same FPS."""
        assembler = RecordAssembler()
        engine_result = _make_engine_result()
        ocr_result = _make_ocr_result()
        stage_result = _make_stage_result()
        groups = _make_engine_groups()

        early = assembler.assemble(
            engine_result, ocr_result, stage_result, groups, 30, 30.0
        )
        late = assembler.assemble(
            engine_result, ocr_result, stage_result, groups, 60, 30.0
        )

        assert early.timestamp == 1_000
        assert late.timestamp == 2_000
        assert late.timestamp > early.timestamp

    def test_timestamp_falls_back_to_frame_number_when_fps_zero(self):
        """When source FPS is unusable (0), the raw frame number is used."""
        assembler = RecordAssembler()
        engine_result = _make_engine_result()
        ocr_result = _make_ocr_result()
        stage_result = _make_stage_result()
        groups = _make_engine_groups()

        record = assembler.assemble(
            engine_result, ocr_result, stage_result, groups, 4242, 0.0
        )

        assert record.timestamp == 4242


# ─── Property-Based Tests (Hypothesis) ────────────────────────────────────────

from hypothesis import given, settings
from hypothesis import strategies as st


# ─── Strategies ───────────────────────────────────────────────────────────────


@st.composite
def engine_status_strategy(draw):
    """Strategy for a single EngineStatus value."""
    return draw(st.sampled_from(list(EngineStatus)))


@st.composite
def ocr_field_status_strategy(draw):
    """Strategy for a single OCRFieldStatus value."""
    return draw(st.sampled_from(list(OCRFieldStatus)))


@st.composite
def ocr_field_result_strategy(draw):
    """Strategy for generating arbitrary OCRFieldResult values.

    Generates consistent combinations of status, raw_text, and parsed_value.
    """
    status = draw(ocr_field_status_strategy())

    if status == OCRFieldStatus.AVAILABLE:
        # Available fields can have raw_text and/or parsed_value
        raw_text = draw(st.one_of(st.none(), st.text(min_size=1, max_size=20)))
        parsed_value = draw(st.one_of(
            st.none(),
            st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
            st.text(min_size=1, max_size=20),
        ))
        return OCRFieldResult(status=status, raw_text=raw_text, parsed_value=parsed_value)
    else:
        # Unavailable or occluded fields have no data
        return OCRFieldResult(status=status, raw_text=None, parsed_value=None)


@st.composite
def numeric_ocr_field_strategy(draw):
    """Strategy for numeric OCR fields (speed/altitude) with consistent data."""
    status = draw(ocr_field_status_strategy())

    if status == OCRFieldStatus.AVAILABLE:
        value = draw(st.floats(min_value=0.0, max_value=50000.0, allow_nan=False, allow_infinity=False))
        return OCRFieldResult(status=status, raw_text=str(value), parsed_value=value)
    else:
        return OCRFieldResult(status=status, raw_text=None, parsed_value=None)


@st.composite
def unit_ocr_field_strategy(draw):
    """Strategy for unit OCR fields (KM/H, KM, MPH, etc.)."""
    status = draw(ocr_field_status_strategy())

    if status == OCRFieldStatus.AVAILABLE:
        unit = draw(st.sampled_from(["KM/H", "KM", "MPH", "M", "FT", "MI"]))
        return OCRFieldResult(status=status, raw_text=unit, parsed_value=unit)
    else:
        return OCRFieldResult(status=status, raw_text=None, parsed_value=None)


@st.composite
def time_ocr_field_strategy(draw):
    """Strategy for time OCR field (T+HH:MM:SS)."""
    status = draw(ocr_field_status_strategy())

    if status == OCRFieldStatus.AVAILABLE:
        hours = draw(st.integers(min_value=0, max_value=23))
        minutes = draw(st.integers(min_value=0, max_value=59))
        seconds = draw(st.integers(min_value=0, max_value=59))
        sign = draw(st.sampled_from(["+", "-"]))
        time_str = f"T{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
        return OCRFieldResult(status=status, raw_text=time_str, parsed_value=time_str)
    else:
        return OCRFieldResult(status=status, raw_text=None, parsed_value=None)


@st.composite
def stage_label_ocr_field_strategy(draw):
    """Strategy for stage label OCR fields."""
    status = draw(ocr_field_status_strategy())

    if status == OCRFieldStatus.AVAILABLE:
        label = draw(st.sampled_from(["SUPER HEAVY", "STARSHIP", "BOOSTER", "SHIP"]))
        return OCRFieldResult(status=status, raw_text=label, parsed_value=label)
    else:
        return OCRFieldResult(status=status, raw_text=None, parsed_value=None)


@st.composite
def ocr_result_strategy(draw):
    """Strategy for generating arbitrary valid OCRResult instances."""
    return OCRResult(
        time=draw(time_ocr_field_strategy()),
        speed_l=draw(numeric_ocr_field_strategy()),
        speed_l_unit=draw(unit_ocr_field_strategy()),
        speed_r=draw(numeric_ocr_field_strategy()),
        speed_r_unit=draw(unit_ocr_field_strategy()),
        altitude_l=draw(numeric_ocr_field_strategy()),
        altitude_l_unit=draw(unit_ocr_field_strategy()),
        altitude_r=draw(numeric_ocr_field_strategy()),
        altitude_r_unit=draw(unit_ocr_field_strategy()),
        stage_l=draw(stage_label_ocr_field_strategy()),
        stage_r=draw(stage_label_ocr_field_strategy()),
        stage_sep_text=draw(ocr_field_result_strategy()),
    )


@st.composite
def engine_statuses_strategy(draw):
    """Strategy for generating all 39 engine statuses (6 starship + 33 superheavy)."""
    statuses: dict[str, EngineStatus] = {}
    for i in range(1, 7):
        statuses[f"e{i}"] = draw(engine_status_strategy())
    for i in range(1, 34):
        statuses[f"sh{i}"] = draw(engine_status_strategy())
    return statuses


@st.composite
def engine_analysis_result_strategy(draw):
    """Strategy for generating arbitrary EngineAnalysisResult."""
    statuses = draw(engine_statuses_strategy())
    accuracy = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    return EngineAnalysisResult(
        engine_statuses=statuses,
        detection_accuracy=accuracy,
        engine_group_bounding_boxes=[],
    )


@st.composite
def stage_assignment_result_strategy(draw):
    """Strategy for generating arbitrary StageAssignmentResult."""
    left = draw(st.sampled_from(["super_heavy", "starship", "booster", "ship"]))
    right = draw(st.sampled_from(["super_heavy", "starship", "booster", "ship"]))
    state = draw(st.sampled_from(list(SeparationState)))
    return StageAssignmentResult(left_stage=left, right_stage=right, separation_state=state)


# ─── Property 12: Telemetry Record Assembly Completeness ──────────────────────
# **Validates: Requirements 6.1, 6.3**


class TestRecordAssemblyCompleteness:
    """Property 12: Telemetry Record Assembly Completeness.

    For any valid combination of engine analysis results, OCR results, and stage
    assignment results, the assembled Telemetry Record SHALL contain all required
    fields: mission_elapsed_time, speed_left (with unit), speed_right (with unit),
    altitude_left (with unit), altitude_right (with unit), stage labels, stage
    assignments, all 39 engine statuses, and detection accuracy.
    """

    @settings(max_examples=100)
    @given(
        engine_result=engine_analysis_result_strategy(),
        ocr_result=ocr_result_strategy(),
        stage_result=stage_assignment_result_strategy(),
    )
    def test_assembled_record_contains_all_required_fields(
        self, engine_result, ocr_result, stage_result
    ):
        """Assembled TelemetryRecord contains all required fields for any valid inputs."""
        assembler = RecordAssembler()
        groups = _make_engine_groups()

        record = assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)

        # Verify record is a TelemetryRecord
        from src.telemetry_record import TelemetryRecord
        assert isinstance(record, TelemetryRecord)

        # mission_elapsed_time is present as a field (may be None if OCR unavailable)
        assert hasattr(record, "mission_elapsed_time")

        # speed_left has value, unit, and status fields
        assert hasattr(record.speed_left, "value")
        assert hasattr(record.speed_left, "unit")
        assert hasattr(record.speed_left, "status")
        assert record.speed_left.status in ("available", "unavailable", "occluded_by_engines")

        # speed_right has value, unit, and status fields
        assert hasattr(record.speed_right, "value")
        assert hasattr(record.speed_right, "unit")
        assert hasattr(record.speed_right, "status")
        assert record.speed_right.status in ("available", "unavailable", "occluded_by_engines")

        # altitude_left has value, unit, and status fields
        assert hasattr(record.altitude_left, "value")
        assert hasattr(record.altitude_left, "unit")
        assert hasattr(record.altitude_left, "status")
        assert record.altitude_left.status in ("available", "unavailable", "occluded_by_engines")

        # altitude_right has value, unit, and status fields
        assert hasattr(record.altitude_right, "value")
        assert hasattr(record.altitude_right, "unit")
        assert hasattr(record.altitude_right, "status")
        assert record.altitude_right.status in ("available", "unavailable", "occluded_by_engines")

        # Stage labels are present as fields (may be None if OCR unavailable)
        assert hasattr(record, "stage_left_label")
        assert hasattr(record, "stage_right_label")

        # Stage assignments are present and non-empty strings
        assert isinstance(record.stage_assignment_left, str)
        assert len(record.stage_assignment_left) > 0
        assert isinstance(record.stage_assignment_right, str)
        assert len(record.stage_assignment_right) > 0

        # Separation state is a valid literal
        assert record.separation_state in ("pre_separation", "post_separation")

        # All 39 engine statuses present (6 starship + 33 superheavy)
        total_engines = len(record.starship_engines) + len(record.superheavy_engines)
        assert total_engines == 39, (
            f"Expected 39 engine statuses, got {total_engines} "
            f"(starship={len(record.starship_engines)}, superheavy={len(record.superheavy_engines)})"
        )

        # Each engine status is a valid literal
        valid_statuses = {"active", "inactive", "undetected"}
        for eid, status in record.starship_engines.items():
            assert status in valid_statuses, f"Invalid status '{status}' for engine '{eid}'"
        for eid, status in record.superheavy_engines.items():
            assert status in valid_statuses, f"Invalid status '{status}' for engine '{eid}'"

        # Detection accuracy is present with per-group floats
        assert hasattr(record, "detection_accuracy")
        assert isinstance(record.detection_accuracy.starship, float)
        assert isinstance(record.detection_accuracy.superheavy, float)
        assert 0.0 <= record.detection_accuracy.starship <= 1.0
        assert 0.0 <= record.detection_accuracy.superheavy <= 1.0

        # Timestamp is a positive integer
        assert isinstance(record.timestamp, int)
        assert record.timestamp > 0

        # Sequence number is a positive integer
        assert isinstance(record.sequence_number, int)
        assert record.sequence_number > 0


# ─── Property 13: Sequence Number Monotonicity ────────────────────────────────
# **Validates: Requirements 6.2**


class TestSequenceNumberMonotonicity:
    """Property 13: Sequence Number Monotonicity.

    For any sequence of assembled Telemetry Records within a session, the
    sequence_number field SHALL be strictly monotonically increasing.
    """

    @settings(max_examples=100)
    @given(
        inputs=st.lists(
            st.tuples(
                engine_analysis_result_strategy(),
                ocr_result_strategy(),
                stage_assignment_result_strategy(),
            ),
            min_size=2,
            max_size=20,
        )
    )
    def test_sequence_numbers_strictly_increasing(self, inputs):
        """Sequence numbers are strictly monotonically increasing across any session."""
        assembler = RecordAssembler()
        groups = _make_engine_groups()

        records = []
        for engine_result, ocr_result, stage_result in inputs:
            record = assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)
            records.append(record)

        # Verify strict monotonic increase
        for i in range(1, len(records)):
            assert records[i].sequence_number > records[i - 1].sequence_number, (
                f"Sequence number not strictly increasing: "
                f"record[{i-1}].seq={records[i-1].sequence_number}, "
                f"record[{i}].seq={records[i].sequence_number}"
            )

    @settings(max_examples=100)
    @given(
        inputs=st.lists(
            st.tuples(
                engine_analysis_result_strategy(),
                ocr_result_strategy(),
                stage_assignment_result_strategy(),
            ),
            min_size=1,
            max_size=20,
        )
    )
    def test_sequence_numbers_start_at_one_and_increment_by_one(self, inputs):
        """Sequence numbers start at 1 and increment by exactly 1 each call."""
        assembler = RecordAssembler()
        groups = _make_engine_groups()

        for idx, (engine_result, ocr_result, stage_result) in enumerate(inputs, start=1):
            record = assembler.assemble(engine_result, ocr_result, stage_result, groups, _TEST_FRAME_NUMBER, _TEST_SOURCE_FPS)
            assert record.sequence_number == idx, (
                f"Expected sequence_number={idx}, got {record.sequence_number}"
            )
