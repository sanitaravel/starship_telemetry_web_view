"""Property-based tests for the Stage Assignment module.

Tests Properties 9, 10, and 11 from the design document:
- Property 9: Pre-Separation Assignment Invariant
- Property 10: Post-Separation Label-Based Assignment
- Property 11: Separation State Monotonicity

**Validates: Requirements 5.1, 5.2, 5.4**
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.enums import OCRFieldStatus, SeparationState
from src.ocr_engine import OCRFieldResult, OCRResult
from src.stage_assignment import StageAssigner, StageAssignmentResult


# ─── Strategies ───────────────────────────────────────────────────────────────


def ocr_field_unavailable_strategy():
    """Strategy for an unavailable OCR field."""
    return st.just(OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE))


def ocr_field_available_strategy(parsed_value=None, raw_text=None):
    """Strategy for an available OCR field with given values."""
    return st.builds(
        OCRFieldResult,
        status=st.just(OCRFieldStatus.AVAILABLE),
        raw_text=st.just(raw_text),
        parsed_value=st.just(parsed_value),
    )


def non_sep_text_strategy():
    """Strategy for stage_sep_text that does NOT contain 'STAGE SEP'.

    Generates text that won't trigger separation detection.
    """
    return st.one_of(
        # Unavailable field - no separation possible
        st.just(OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)),
        # Occluded field - no separation possible
        st.just(OCRFieldResult(status=OCRFieldStatus.OCCLUDED_BY_ENGINES)),
        # Available but with text that doesn't contain "STAGE SEP"
        st.builds(
            OCRFieldResult,
            status=st.just(OCRFieldStatus.AVAILABLE),
            raw_text=st.sampled_from(["", "SPEED", "ALTITUDE", "T+00:01:00", "NOMINAL", None]),
            parsed_value=st.just(None),
        ),
    )


def sep_text_strategy():
    """Strategy for stage_sep_text that DOES contain 'STAGE SEP'.

    Generates various forms of valid separation text.
    """
    sep_texts = ["STAGE SEP", "stage sep", "Stage Sep", "STAGE SEPARATION", "STAGE SEP CONFIRMED"]
    return st.builds(
        OCRFieldResult,
        status=st.just(OCRFieldStatus.AVAILABLE),
        raw_text=st.sampled_from(sep_texts),
        parsed_value=st.just(None),
    )


def stage_label_strategy():
    """Strategy for stage label text (non-empty, realistic stage names)."""
    return st.sampled_from([
        "SUPER HEAVY", "STARSHIP", "Super Heavy", "Starship",
        "BOOSTER", "SHIP", "Stage 1", "Stage 2",
    ])


def ocr_field_with_label_strategy():
    """Strategy for a stage label field that is available with non-empty text."""
    return st.builds(
        OCRFieldResult,
        status=st.just(OCRFieldStatus.AVAILABLE),
        raw_text=stage_label_strategy(),
        parsed_value=st.just(None),
    )


def generic_ocr_field_strategy():
    """Strategy for a generic OCR field (speed, altitude, time, etc.)."""
    return st.one_of(
        st.just(OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)),
        st.just(OCRFieldResult(status=OCRFieldStatus.OCCLUDED_BY_ENGINES)),
        st.builds(
            OCRFieldResult,
            status=st.just(OCRFieldStatus.AVAILABLE),
            raw_text=st.just("100"),
            parsed_value=st.just(100.0),
        ),
    )


def pre_separation_ocr_result_strategy():
    """Strategy for OCR results that will NOT trigger separation.

    All fields have realistic values but stage_sep_text never contains 'STAGE SEP'.
    """
    return st.builds(
        OCRResult,
        time=generic_ocr_field_strategy(),
        speed_l=generic_ocr_field_strategy(),
        speed_l_unit=generic_ocr_field_strategy(),
        speed_r=generic_ocr_field_strategy(),
        speed_r_unit=generic_ocr_field_strategy(),
        altitude_l=generic_ocr_field_strategy(),
        altitude_l_unit=generic_ocr_field_strategy(),
        altitude_r=generic_ocr_field_strategy(),
        altitude_r_unit=generic_ocr_field_strategy(),
        stage_l=generic_ocr_field_strategy(),
        stage_r=generic_ocr_field_strategy(),
        stage_sep_text=non_sep_text_strategy(),
    )


def post_separation_ocr_result_with_labels_strategy():
    """Strategy for OCR results post-separation with both stage labels available."""
    return st.builds(
        OCRResult,
        time=generic_ocr_field_strategy(),
        speed_l=generic_ocr_field_strategy(),
        speed_l_unit=generic_ocr_field_strategy(),
        speed_r=generic_ocr_field_strategy(),
        speed_r_unit=generic_ocr_field_strategy(),
        altitude_l=generic_ocr_field_strategy(),
        altitude_l_unit=generic_ocr_field_strategy(),
        altitude_r=generic_ocr_field_strategy(),
        altitude_r_unit=generic_ocr_field_strategy(),
        stage_l=ocr_field_with_label_strategy(),
        stage_r=ocr_field_with_label_strategy(),
        stage_sep_text=non_sep_text_strategy(),
    )


def separation_triggering_ocr_result_strategy():
    """Strategy for OCR results that WILL trigger separation."""
    return st.builds(
        OCRResult,
        time=generic_ocr_field_strategy(),
        speed_l=generic_ocr_field_strategy(),
        speed_l_unit=generic_ocr_field_strategy(),
        speed_r=generic_ocr_field_strategy(),
        speed_r_unit=generic_ocr_field_strategy(),
        altitude_l=generic_ocr_field_strategy(),
        altitude_l_unit=generic_ocr_field_strategy(),
        altitude_r=generic_ocr_field_strategy(),
        altitude_r_unit=generic_ocr_field_strategy(),
        stage_l=generic_ocr_field_strategy(),
        stage_r=generic_ocr_field_strategy(),
        stage_sep_text=sep_text_strategy(),
    )


# ─── Property 9: Pre-Separation Assignment Invariant ──────────────────────────
# **Validates: Requirements 5.1**


class TestPreSeparationAssignment:
    """Property 9: Pre-Separation Assignment Invariant.

    For any sequence of OCR results where stage_sep_text has never contained
    "STAGE SEP", all telemetry SHALL be assigned to "super_heavy".
    """

    @given(
        ocr_results=st.lists(
            pre_separation_ocr_result_strategy(),
            min_size=1,
            max_size=20,
        )
    )
    def test_all_assigned_to_super_heavy_before_separation(self, ocr_results):
        """All left and right stage assignments are 'super_heavy' when no separation detected."""
        assigner = StageAssigner()

        for ocr_result in ocr_results:
            result = assigner.assign(ocr_result)

            assert result.left_stage == "super_heavy", (
                f"Expected left_stage='super_heavy', got '{result.left_stage}'"
            )
            assert result.right_stage == "super_heavy", (
                f"Expected right_stage='super_heavy', got '{result.right_stage}'"
            )
            assert result.separation_state == SeparationState.PRE_SEPARATION

    @given(ocr_result=pre_separation_ocr_result_strategy())
    def test_single_frame_pre_separation(self, ocr_result):
        """A single frame without separation text assigns to 'super_heavy'."""
        assigner = StageAssigner()
        result = assigner.assign(ocr_result)

        assert result.left_stage == "super_heavy"
        assert result.right_stage == "super_heavy"
        assert result.separation_state == SeparationState.PRE_SEPARATION

    @given(
        ocr_results=st.lists(
            pre_separation_ocr_result_strategy(),
            min_size=1,
            max_size=20,
        )
    )
    def test_separation_state_stays_pre_without_trigger(self, ocr_results):
        """Separation state remains PRE_SEPARATION without a trigger event."""
        assigner = StageAssigner()

        for ocr_result in ocr_results:
            assigner.assign(ocr_result)

        assert assigner.get_separation_state() == SeparationState.PRE_SEPARATION


# ─── Property 10: Post-Separation Label-Based Assignment ──────────────────────
# **Validates: Requirements 5.2**


class TestPostSeparationLabelAssignment:
    """Property 10: Post-Separation Label-Based Assignment.

    For any OCR result where separation has occurred and both stage_L and stage_R
    contain non-empty text, left-side SHALL be assigned to stage_L label and
    right-side to stage_R label.
    """

    @given(
        sep_trigger=separation_triggering_ocr_result_strategy(),
        labeled_results=st.lists(
            post_separation_ocr_result_with_labels_strategy(),
            min_size=1,
            max_size=10,
        ),
    )
    def test_labels_used_after_separation(self, sep_trigger, labeled_results):
        """After separation, stage labels from OCR are used for assignment."""
        assigner = StageAssigner()

        # Trigger separation
        assigner.assign(sep_trigger)
        assert assigner.get_separation_state() == SeparationState.POST_SEPARATION

        # Process labeled results
        for ocr_result in labeled_results:
            result = assigner.assign(ocr_result)

            # Expected: normalized label from stage_l/stage_r
            expected_left = self._normalize(self._get_label(ocr_result.stage_l))
            expected_right = self._normalize(self._get_label(ocr_result.stage_r))

            assert result.left_stage == expected_left, (
                f"Expected left_stage='{expected_left}', got '{result.left_stage}'"
            )
            assert result.right_stage == expected_right, (
                f"Expected right_stage='{expected_right}', got '{result.right_stage}'"
            )
            assert result.separation_state == SeparationState.POST_SEPARATION

    @given(
        sep_trigger=separation_triggering_ocr_result_strategy(),
        left_label=stage_label_strategy(),
        right_label=stage_label_strategy(),
    )
    def test_label_normalization_applied(self, sep_trigger, left_label, right_label):
        """Labels are normalized: lowercased and spaces replaced with underscores."""
        assigner = StageAssigner()

        # Trigger separation
        assigner.assign(sep_trigger)

        # Build OCR result with specific labels
        ocr_result = OCRResult(
            time=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
            speed_l=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
            speed_l_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
            speed_r=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
            speed_r_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
            altitude_l=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
            altitude_l_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
            altitude_r=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
            altitude_r_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
            stage_l=OCRFieldResult(status=OCRFieldStatus.AVAILABLE, raw_text=left_label),
            stage_r=OCRFieldResult(status=OCRFieldStatus.AVAILABLE, raw_text=right_label),
            stage_sep_text=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
        )

        result = assigner.assign(ocr_result)

        assert result.left_stage == left_label.lower().replace(" ", "_")
        assert result.right_stage == right_label.lower().replace(" ", "_")

    @staticmethod
    def _normalize(label: str) -> str:
        """Mirror the normalization logic from StageAssigner."""
        return label.lower().replace(" ", "_")

    @staticmethod
    def _get_label(field: OCRFieldResult) -> str:
        """Extract label text from an OCR field (mirrors StageAssigner._get_stage_label)."""
        value = field.parsed_value if field.parsed_value else field.raw_text
        return str(value).strip()


# ─── Property 11: Separation State Monotonicity ───────────────────────────────
# **Validates: Requirements 5.4**


class TestSeparationStateMonotonicity:
    """Property 11: Separation State Monotonicity.

    Once separation state transitions to "post_separation", it SHALL never revert
    to "pre_separation" regardless of subsequent OCR readings.
    """

    @given(
        pre_results=st.lists(
            pre_separation_ocr_result_strategy(),
            min_size=0,
            max_size=5,
        ),
        sep_trigger=separation_triggering_ocr_result_strategy(),
        post_results=st.lists(
            pre_separation_ocr_result_strategy(),
            min_size=1,
            max_size=20,
        ),
    )
    def test_state_never_reverts_after_separation(self, pre_results, sep_trigger, post_results):
        """After transition to POST_SEPARATION, state never reverts regardless of input."""
        assigner = StageAssigner()

        # Process pre-separation frames
        for ocr_result in pre_results:
            assigner.assign(ocr_result)
            assert assigner.get_separation_state() == SeparationState.PRE_SEPARATION

        # Trigger separation
        assigner.assign(sep_trigger)
        assert assigner.get_separation_state() == SeparationState.POST_SEPARATION

        # Process post-separation frames (none contain "STAGE SEP")
        for ocr_result in post_results:
            assigner.assign(ocr_result)
            assert assigner.get_separation_state() == SeparationState.POST_SEPARATION

    @given(
        sep_trigger=separation_triggering_ocr_result_strategy(),
        subsequent_results=st.lists(
            st.one_of(
                pre_separation_ocr_result_strategy(),
                separation_triggering_ocr_result_strategy(),
            ),
            min_size=1,
            max_size=20,
        ),
    )
    def test_state_stays_post_even_with_repeated_sep_signals(self, sep_trigger, subsequent_results):
        """State remains POST_SEPARATION even when more separation signals arrive."""
        assigner = StageAssigner()

        # Trigger initial separation
        assigner.assign(sep_trigger)
        assert assigner.get_separation_state() == SeparationState.POST_SEPARATION

        # Process mix of separation and non-separation frames
        for ocr_result in subsequent_results:
            assigner.assign(ocr_result)
            assert assigner.get_separation_state() == SeparationState.POST_SEPARATION

    @given(
        num_frames=st.integers(min_value=5, max_value=30),
        sep_index=st.integers(min_value=0, max_value=4),
    )
    def test_monotonic_transition_at_arbitrary_point(self, num_frames, sep_index):
        """Separation can happen at any point in the stream; once it does, it's permanent."""
        assume(sep_index < num_frames)

        assigner = StageAssigner()

        for i in range(num_frames):
            if i == sep_index:
                # This frame triggers separation
                ocr_result = OCRResult(
                    time=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    speed_l=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    speed_l_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    speed_r=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    speed_r_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    altitude_l=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    altitude_l_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    altitude_r=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    altitude_r_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    stage_l=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    stage_r=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    stage_sep_text=OCRFieldResult(
                        status=OCRFieldStatus.AVAILABLE, raw_text="STAGE SEP"
                    ),
                )
            else:
                # Non-separation frame
                ocr_result = OCRResult(
                    time=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    speed_l=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    speed_l_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    speed_r=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    speed_r_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    altitude_l=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    altitude_l_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    altitude_r=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    altitude_r_unit=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    stage_l=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    stage_r=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                    stage_sep_text=OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE),
                )

            result = assigner.assign(ocr_result)

            if i < sep_index:
                assert result.separation_state == SeparationState.PRE_SEPARATION
            else:
                # From sep_index onwards, always POST_SEPARATION
                assert result.separation_state == SeparationState.POST_SEPARATION
