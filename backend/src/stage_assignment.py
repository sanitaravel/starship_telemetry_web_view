"""Stage Assignment - attributes telemetry to the correct vehicle stage.

Tracks session-level separation state and assigns left/right telemetry
data to the vehicle shown on that side of the HUD ("super_heavy" or
"starship") based on whether stage separation has occurred and on the
stage labels under the gauges.

Assignment is stateful (separation, and each side's last known vehicle), so
frames must be assigned in video order; the pipeline does this as results
leave its reorder buffer.
"""

import re
from dataclasses import dataclass

from src.enums import OCRFieldStatus, SeparationState
from src.ocr_engine import OCRFieldResult, OCRResult

SUPER_HEAVY = "super_heavy"
STARSHIP = "starship"

_SIDES = ("left", "right")


@dataclass
class StageAssignmentResult:
    """Result of stage assignment for a single frame.

    Attributes:
        left_stage: Vehicle for left-side telemetry ("super_heavy" or "starship").
        right_stage: Vehicle for right-side telemetry ("super_heavy" or "starship").
        separation_state: Current separation state after processing this frame.
    """

    left_stage: str
    right_stage: str
    separation_state: SeparationState


def label_vehicle(label: str | None) -> str | None:
    """The vehicle a stage label names, tolerating OCR noise.

    "SUPER HEAVY", "~SUPER HEAVY" or "BOOSTER" -> "super_heavy";
    "STARSHIP", "~STARSHIP" or "SHIP" -> "starship"; anything else -> None.
    """
    letters = re.sub(r"[^A-Z]", "", (label or "").upper())
    if "SUPER" in letters or "HEAVY" in letters or "BOOSTER" in letters:
        return SUPER_HEAVY
    if "STAR" in letters or "SHIP" in letters:
        return STARSHIP
    return None


class StageAssigner:
    """Determines stage assignment based on OCR results and session state.

    The separation state is a session-level flag that transitions from
    PRE_SEPARATION to POST_SEPARATION once "STAGE SEP" is detected, and
    never reverts back.

    Assignment logic:
    - Pre-separation: both sides show the stacked vehicle, "super_heavy".
    - Post-separation, each side on its own:
      1. a readable stage label names the vehicle (and is remembered);
      2. otherwise the side keeps its last labelled vehicle while its gauges
         stay up (labels are often unreadable for a few frames);
      3. otherwise, if the other side's vehicle is known, this side is the
         other vehicle;
      4. otherwise "starship" (after the booster lands, the remaining gauges
         are unlabelled and belong to Starship).
    A side's memory is cleared when its readings disappear (no value with a
    unit), e.g. when the HUD switches that side to another vehicle or a graphic.
    """

    def __init__(self) -> None:
        self._separation_state = SeparationState.PRE_SEPARATION
        # Vehicle last identified on each side while its gauges stay up
        self._side_vehicle: dict[str, str | None] = {side: None for side in _SIDES}

    def assign(self, ocr_result: OCRResult) -> StageAssignmentResult:
        """Determine stage assignment for the next frame, in video order.

        Args:
            ocr_result: OCR extraction results for the current frame.

        Returns:
            StageAssignmentResult with left/right vehicles and current state.
        """
        # Step 1: Check for separation event - transition is one-way
        if self._separation_state == SeparationState.PRE_SEPARATION:
            if self._detect_separation(ocr_result):
                self._separation_state = SeparationState.POST_SEPARATION

        # Step 2: Pre-separation, both sides show the stack
        if self._separation_state == SeparationState.PRE_SEPARATION:
            return StageAssignmentResult(
                left_stage=SUPER_HEAVY,
                right_stage=SUPER_HEAVY,
                separation_state=self._separation_state,
            )

        # Step 3: Post-separation, per side: label, then memory
        labels = {
            "left": label_vehicle(self._get_stage_label(ocr_result.stage_l)),
            "right": label_vehicle(self._get_stage_label(ocr_result.stage_r)),
        }
        gauges = {
            "left": self._has_readings(
                (ocr_result.speed_l, ocr_result.speed_l_unit),
                (ocr_result.altitude_l, ocr_result.altitude_l_unit),
            ),
            "right": self._has_readings(
                (ocr_result.speed_r, ocr_result.speed_r_unit),
                (ocr_result.altitude_r, ocr_result.altitude_r_unit),
            ),
        }
        known: dict[str, str | None] = {}
        for side in _SIDES:
            if labels[side] is not None:
                self._side_vehicle[side] = labels[side]
            elif not gauges[side]:
                self._side_vehicle[side] = None
            known[side] = self._side_vehicle[side]

        # Step 4: Infer an unknown side from the other one, else default
        stages = {}
        for side, other in (("left", "right"), ("right", "left")):
            if known[side] is not None:
                stages[side] = known[side]
            elif known[other] is not None:
                stages[side] = STARSHIP if known[other] == SUPER_HEAVY else SUPER_HEAVY
            else:
                stages[side] = STARSHIP

        return StageAssignmentResult(
            left_stage=stages["left"],
            right_stage=stages["right"],
            separation_state=self._separation_state,
        )

    def get_separation_state(self) -> SeparationState:
        """Return current separation state."""
        return self._separation_state

    def reset(self) -> None:
        """Reset separation state and side memory for a new run."""
        self._separation_state = SeparationState.PRE_SEPARATION
        self._side_vehicle = {side: None for side in _SIDES}

    def _detect_separation(self, ocr_result: OCRResult) -> bool:
        """Check if stage_sep_text indicates separation has occurred.

        Returns True if the stage_sep_text field is available and contains
        "STAGE SEP" (case-insensitive).
        """
        sep_field = ocr_result.stage_sep_text
        if sep_field.status != OCRFieldStatus.AVAILABLE:
            return False

        # Check parsed_value first, fall back to raw_text
        text = sep_field.parsed_value if sep_field.parsed_value else sep_field.raw_text
        if text is None:
            return False

        return "STAGE SEP" in str(text).upper()

    @staticmethod
    def _has_readings(*gauges: tuple[OCRFieldResult, OCRFieldResult]) -> bool:
        """Whether a side shows telemetry: a value read together with its unit.

        Units alone are not enough: when the HUD hands a side over to another
        vehicle, its gauges blank out for a moment but keep their unit
        captions. The engine graphic or the globe that replace a side's gauges
        never count either (values without a unit).
        """
        return any(
            value.status == OCRFieldStatus.AVAILABLE and unit.status == OCRFieldStatus.AVAILABLE
            for value, unit in gauges
        )

    def _get_stage_label(self, field: OCRFieldResult) -> str | None:
        """Extract a stage label from an OCR field result.

        Returns the label string if the field is available and has a
        non-empty value, otherwise None.
        """
        if field.status != OCRFieldStatus.AVAILABLE:
            return None

        # Use parsed_value first, fall back to raw_text
        value = field.parsed_value if field.parsed_value else field.raw_text
        if value is None:
            return None

        label = str(value).strip()
        return label if label else None
