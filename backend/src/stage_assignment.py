"""Stage Assignment - attributes telemetry to the correct vehicle stage.

Tracks session-level separation state and assigns left/right telemetry
data to the appropriate stage (super_heavy, starship, or as labeled by
stage_L/stage_R OCR text) based on whether stage separation has occurred.
"""

from dataclasses import dataclass

from src.enums import OCRFieldStatus, SeparationState
from src.ocr_engine import OCRResult


@dataclass
class StageAssignmentResult:
    """Result of stage assignment for a single frame.

    Attributes:
        left_stage: Stage name for left-side telemetry
            ("super_heavy", "starship", or as labeled by stage_L).
        right_stage: Stage name for right-side telemetry
            ("super_heavy", "starship", or as labeled by stage_R).
        separation_state: Current separation state after processing this frame.
    """

    left_stage: str
    right_stage: str
    separation_state: SeparationState


class StageAssigner:
    """Determines stage assignment based on OCR results and session state.

    The separation state is a session-level flag that transitions from
    PRE_SEPARATION to POST_SEPARATION once "STAGE SEP" is detected, and
    never reverts back.

    Assignment logic:
    - Pre-separation: all data assigned to "super_heavy".
    - Post-separation with labels: assign per stage_L / stage_R text.
    - Post-separation without labels: all data assigned to "starship".
    """

    def __init__(self) -> None:
        self._separation_state = SeparationState.PRE_SEPARATION

    def assign(self, ocr_result: OCRResult) -> StageAssignmentResult:
        """Determine stage assignment based on OCR result and session state.

        Checks the stage_sep_text field for "STAGE SEP" to transition the
        separation state. Then assigns stages based on current state and
        availability of stage labels.

        Args:
            ocr_result: OCR extraction results for the current frame.

        Returns:
            StageAssignmentResult with left/right stage names and current state.
        """
        # Step 1: Check for separation event - transition is one-way
        if self._separation_state == SeparationState.PRE_SEPARATION:
            if self._detect_separation(ocr_result):
                self._separation_state = SeparationState.POST_SEPARATION

        # Step 2: Assign stages based on current separation state
        if self._separation_state == SeparationState.PRE_SEPARATION:
            # Req 5.1: Pre-separation, all data goes to super_heavy
            return StageAssignmentResult(
                left_stage="super_heavy",
                right_stage="super_heavy",
                separation_state=self._separation_state,
            )

        # Post-separation: check if stage labels are available
        left_label = self._get_stage_label(ocr_result.stage_l)
        right_label = self._get_stage_label(ocr_result.stage_r)

        if left_label and right_label:
            # Req 5.2: Labels available, use them
            return StageAssignmentResult(
                left_stage=self._normalize_label(left_label),
                right_stage=self._normalize_label(right_label),
                separation_state=self._separation_state,
            )

        # Req 5.3: Labels unavailable, default to starship
        return StageAssignmentResult(
            left_stage="starship",
            right_stage="starship",
            separation_state=self._separation_state,
        )

    def get_separation_state(self) -> SeparationState:
        """Return current separation state."""
        return self._separation_state

    def reset(self) -> None:
        """Reset separation state to pre_separation."""
        self._separation_state = SeparationState.PRE_SEPARATION

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

    def _get_stage_label(self, field) -> str | None:
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

    def _normalize_label(self, label: str) -> str:
        """Normalize a stage label to a consistent format.

        Converts to lowercase and replaces spaces with underscores.
        E.g., "SUPER HEAVY" -> "super_heavy", "STARSHIP" -> "starship".
        """
        return label.lower().replace(" ", "_")
