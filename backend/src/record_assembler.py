"""Record Assembler - combines pipeline outputs into a TelemetryRecord.

Takes results from engine analysis, OCR extraction, and stage assignment,
and assembles them into a single TelemetryRecord with a monotonically
increasing sequence number and current timestamp.
"""

import time

from src.engine_analyzer import EngineAnalysisResult
from src.enums import EngineStatus, OCRFieldStatus
from src.models import EngineGroup
from src.ocr_engine import OCRFieldResult, OCRResult
from src.stage_assignment import StageAssignmentResult
from src.telemetry_record import DetectionAccuracy, TelemetryFieldValue, TelemetryRecord


# Engine count constants
_STARSHIP_ENGINE_COUNT = 6
_SUPERHEAVY_ENGINE_COUNT = 33


def _ocr_field_to_telemetry_field(
    value_field: OCRFieldResult, unit_field: OCRFieldResult
) -> TelemetryFieldValue:
    """Map an OCR value field and its unit field to a TelemetryFieldValue.

    Args:
        value_field: The OCR field result containing the numeric value.
        unit_field: The OCR field result containing the unit string.

    Returns:
        TelemetryFieldValue with value, unit, and status.
    """
    # Map OCRFieldStatus enum to the string literal expected by TelemetryFieldValue
    status: str = value_field.status.value

    value: float | None = None
    if value_field.status == OCRFieldStatus.AVAILABLE and value_field.parsed_value is not None:
        value = float(value_field.parsed_value)

    unit: str | None = None
    if unit_field.status == OCRFieldStatus.AVAILABLE:
        # Unit comes from parsed_value or raw_text
        unit_val = unit_field.parsed_value if unit_field.parsed_value else unit_field.raw_text
        if unit_val is not None:
            unit = str(unit_val)

    return TelemetryFieldValue(value=value, unit=unit, status=status)


def _extract_field_text(field: OCRFieldResult) -> str | None:
    """Extract a text string from an OCR field, or None if unavailable."""
    if field.status != OCRFieldStatus.AVAILABLE:
        return None
    if field.parsed_value is not None:
        return str(field.parsed_value)
    return field.raw_text


def _split_engine_statuses(
    engine_statuses: dict[str, EngineStatus],
    engine_groups: list[EngineGroup],
) -> tuple[dict[str, str], dict[str, str]]:
    """Split the combined engine status map into starship and superheavy dicts.

    Uses engine group IDs to determine which engines belong to which vehicle.
    Engine IDs within a starship group go to starship_engines, and those in a
    superheavy group go to superheavy_engines.

    Args:
        engine_statuses: Combined dict of all engine statuses keyed by engine ID.
        engine_groups: Engine groups from ROI configuration (used to classify).

    Returns:
        Tuple of (starship_engines, superheavy_engines) dicts mapping
        engine_id to status string literal.
    """
    starship_ids: set[str] = set()
    superheavy_ids: set[str] = set()

    for group in engine_groups:
        is_starship = "starship" in group.group_id.lower()
        for subgroup in group.subgroups:
            for circle in subgroup.circles:
                if is_starship:
                    starship_ids.add(circle.id)
                else:
                    superheavy_ids.add(circle.id)

    starship_engines: dict[str, str] = {}
    superheavy_engines: dict[str, str] = {}

    for engine_id, status in engine_statuses.items():
        status_str = status.value
        if engine_id in starship_ids:
            starship_engines[engine_id] = status_str
        elif engine_id in superheavy_ids:
            superheavy_engines[engine_id] = status_str
        else:
            # If engine groups aren't provided or engine can't be classified,
            # use count-based heuristic: first 6 are starship, rest superheavy
            if len(starship_engines) < _STARSHIP_ENGINE_COUNT:
                starship_engines[engine_id] = status_str
            else:
                superheavy_engines[engine_id] = status_str

    return starship_engines, superheavy_engines


def _compute_detection_accuracy(
    engine_statuses: dict[str, EngineStatus],
    engine_groups: list[EngineGroup],
) -> DetectionAccuracy:
    """Compute per-group detection accuracy.

    Detection accuracy is the ratio of detected (non-UNDETECTED) engines
    to total expected engines for each group.

    Args:
        engine_statuses: Combined dict of all engine statuses.
        engine_groups: Engine groups from ROI configuration.

    Returns:
        DetectionAccuracy with per-group float values.
    """
    starship_ids: set[str] = set()
    superheavy_ids: set[str] = set()

    for group in engine_groups:
        is_starship = "starship" in group.group_id.lower()
        for subgroup in group.subgroups:
            for circle in subgroup.circles:
                if is_starship:
                    starship_ids.add(circle.id)
                else:
                    superheavy_ids.add(circle.id)

    starship_detected = sum(
        1 for eid in starship_ids
        if eid in engine_statuses and engine_statuses[eid] != EngineStatus.UNDETECTED
    )
    superheavy_detected = sum(
        1 for eid in superheavy_ids
        if eid in engine_statuses and engine_statuses[eid] != EngineStatus.UNDETECTED
    )

    starship_total = len(starship_ids) if starship_ids else _STARSHIP_ENGINE_COUNT
    superheavy_total = len(superheavy_ids) if superheavy_ids else _SUPERHEAVY_ENGINE_COUNT

    return DetectionAccuracy(
        starship=starship_detected / starship_total if starship_total > 0 else 0.0,
        superheavy=superheavy_detected / superheavy_total if superheavy_total > 0 else 0.0,
    )


class RecordAssembler:
    """Assembles pipeline outputs into TelemetryRecord instances.

    Maintains a monotonically increasing sequence counter that increments
    with each assembled record.
    """

    def __init__(self) -> None:
        self._sequence_counter: int = 0

    def assemble(
        self,
        engine_result: EngineAnalysisResult,
        ocr_result: OCRResult,
        stage_result: StageAssignmentResult,
        engine_groups: list[EngineGroup],
        t_zero_found: bool = False,
        stage_sep_found: bool = False,
    ) -> TelemetryRecord:
        """Assemble a TelemetryRecord from pipeline component outputs.

        Combines engine analysis, OCR extraction, and stage assignment results
        into a single structured record. Assigns the next sequence number and
        current timestamp.

        Args:
            engine_result: Output from the Engine Analyzer.
            ocr_result: Output from the OCR Engine.
            stage_result: Output from the Stage Assigner.
            engine_groups: Engine groups from ROI configuration (for splitting).
            t_zero_found: Whether T-0 (00:00:00) has been detected in this session.
            stage_sep_found: Whether stage separation has been detected in this session.

        Returns:
            A fully populated TelemetryRecord.
        """
        self._sequence_counter += 1

        # Map OCR fields to TelemetryFieldValues (value + unit)
        speed_left = _ocr_field_to_telemetry_field(ocr_result.speed_l, ocr_result.speed_l_unit)
        speed_right = _ocr_field_to_telemetry_field(ocr_result.speed_r, ocr_result.speed_r_unit)
        altitude_left = _ocr_field_to_telemetry_field(ocr_result.altitude_l, ocr_result.altitude_l_unit)
        altitude_right = _ocr_field_to_telemetry_field(ocr_result.altitude_r, ocr_result.altitude_r_unit)

        # Extract text fields
        mission_elapsed_time = _extract_field_text(ocr_result.time)
        mission_elapsed_time_raw = ocr_result.time.raw_text if ocr_result.time.raw_text else None
        stage_left_label = _extract_field_text(ocr_result.stage_l)
        stage_right_label = _extract_field_text(ocr_result.stage_r)
        stage_separation_text = _extract_field_text(ocr_result.stage_sep_text)

        # Split engine statuses into per-vehicle dicts
        starship_engines, superheavy_engines = _split_engine_statuses(
            engine_result.engine_statuses, engine_groups
        )

        # Compute per-group detection accuracy
        detection_accuracy = _compute_detection_accuracy(
            engine_result.engine_statuses, engine_groups
        )

        # Current timestamp in Unix milliseconds
        timestamp = int(time.time() * 1000)

        return TelemetryRecord(
            sequence_number=self._sequence_counter,
            mission_elapsed_time=mission_elapsed_time,
            mission_elapsed_time_raw=mission_elapsed_time_raw,
            speed_left=speed_left,
            speed_right=speed_right,
            altitude_left=altitude_left,
            altitude_right=altitude_right,
            stage_left_label=stage_left_label,
            stage_right_label=stage_right_label,
            stage_separation_text=stage_separation_text,
            stage_assignment_left=stage_result.left_stage,
            stage_assignment_right=stage_result.right_stage,
            separation_state=stage_result.separation_state.value,
            t_zero_found=t_zero_found,
            stage_sep_found=stage_sep_found,
            starship_engines=starship_engines,
            superheavy_engines=superheavy_engines,
            detection_accuracy=detection_accuracy,
            timestamp=timestamp,
        )

    @property
    def current_sequence(self) -> int:
        """Return the current sequence number (last assigned)."""
        return self._sequence_counter

    def reset(self) -> None:
        """Reset the sequence counter to zero."""
        self._sequence_counter = 0
