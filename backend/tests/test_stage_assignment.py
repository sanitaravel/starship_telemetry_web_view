"""Property-based tests for the Stage Assignment module.

Tests Properties 9, 10, and 11 from the design document:
- Property 9: Pre-Separation Assignment Invariant
- Property 10: Post-Separation Label-Based Assignment
- Property 11: Separation State Monotonicity

**Validates: Requirements 5.1, 5.2, 5.4**
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.core.enums import OCRFieldStatus, SeparationState
from src.ocr.results import OCRFieldResult, OCRResult
from src.telemetry.stage_assignment import StageAssigner, StageAssignmentResult, label_vehicle


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


# Label text as OCR reads it, with the vehicle it names
VEHICLE_LABELS = {
    "SUPER HEAVY": "super_heavy",
    "Super Heavy": "super_heavy",
    "~SUPER HEAVY": "super_heavy",
    "BOOSTER": "super_heavy",
    "STARSHIP": "starship",
    "Starship": "starship",
    "~STARSHIP": "starship",
    "SHIP": "starship",
}


def stage_label_strategy():
    """Strategy for stage label text that names a vehicle (as OCR reads it)."""
    return st.sampled_from(sorted(VEHICLE_LABELS))


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
    name a vehicle, left-side SHALL be assigned to the vehicle stage_L names and
    right-side to the vehicle stage_R names.
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

            # Expected: the vehicle each label names
            expected_left = VEHICLE_LABELS[self._get_label(ocr_result.stage_l)]
            expected_right = VEHICLE_LABELS[self._get_label(ocr_result.stage_r)]

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
    def test_labels_map_to_vehicles(self, sep_trigger, left_label, right_label):
        """Labels map to "super_heavy" or "starship", tolerating OCR noise."""
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

        assert result.left_stage == VEHICLE_LABELS[left_label]
        assert result.right_stage == VEHICLE_LABELS[right_label]

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


# ─── Post-separation side tracking (IFT replay scenarios) ─────────────────────


_UNAVAILABLE = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)


def _frame(left_label=None, right_label=None, left_gauges=True, right_gauges=True, sep=False):
    """An OCR result with optional stage labels and readings per side.

    A side without readings still shows its unit captions, as the HUD does
    while it hands that side over to another vehicle.
    """
    def label(text):
        return OCRFieldResult(status=OCRFieldStatus.AVAILABLE, raw_text=text) if text else _UNAVAILABLE

    def value(shown):
        return OCRFieldResult(status=OCRFieldStatus.AVAILABLE, raw_text="123", parsed_value=123.0) if shown else _UNAVAILABLE

    def unit(text):
        return OCRFieldResult(status=OCRFieldStatus.AVAILABLE, raw_text=text, parsed_value=text)

    return OCRResult(
        time=_UNAVAILABLE,
        speed_l=value(left_gauges),
        speed_l_unit=unit("KM/H"),
        speed_r=value(right_gauges),
        speed_r_unit=unit("KM/H"),
        altitude_l=value(left_gauges),
        altitude_l_unit=unit("KM"),
        altitude_r=value(right_gauges),
        altitude_r_unit=unit("KM"),
        stage_l=label(left_label),
        stage_r=label(right_label),
        stage_sep_text=OCRFieldResult(status=OCRFieldStatus.AVAILABLE, raw_text="STAGE SEP") if sep else _UNAVAILABLE,
    )


def _separated() -> StageAssigner:
    assigner = StageAssigner()
    assigner.assign(_frame(sep=True, left_gauges=False))
    return assigner


def _stages(result: StageAssignmentResult) -> tuple[str, str]:
    return result.left_stage, result.right_stage


class TestPostSeparationSideTracking:
    def test_each_side_uses_its_own_label(self):
        # Right label unreadable: the left booster gauges must not become Starship
        assigner = _separated()
        assert _stages(assigner.assign(_frame("SUPER HEAVY", None))) == ("super_heavy", "starship")

    def test_noisy_label_names_its_vehicle(self):
        assigner = _separated()
        assert _stages(assigner.assign(_frame("SUPER HEAVY", "~STARSHIP"))) == ("super_heavy", "starship")

    def test_side_keeps_its_vehicle_while_its_label_is_unreadable(self):
        assigner = _separated()
        assigner.assign(_frame("SUPER HEAVY", "STARSHIP"))
        assert _stages(assigner.assign(_frame(None, None))) == ("super_heavy", "starship")

    def test_unknown_side_is_the_other_vehicle(self):
        assigner = _separated()
        assert _stages(assigner.assign(_frame(None, "STARSHIP"))) == ("super_heavy", "starship")

    def test_one_missed_reading_keeps_the_side_through_the_other_label(self):
        # A frame where OCR misses the left readings clears its memory, but the
        # right side is still known as Starship, so the left stays Super Heavy
        assigner = _separated()
        assigner.assign(_frame("SUPER HEAVY", "STARSHIP"))
        assigner.assign(_frame(None, None, left_gauges=False))
        assert _stages(assigner.assign(_frame(None, None))) == ("super_heavy", "starship")

    def test_side_forgets_its_vehicle_when_its_gauges_disappear(self):
        # T+07:04 on the replay: the booster has landed, the left gauges blank
        # out (captions stay) and come back unlabelled showing Starship; the
        # right side shows graphics only
        assigner = _separated()
        assigner.assign(_frame("SUPER HEAVY", "STARSHIP"))
        assigner.assign(_frame(None, None, left_gauges=False, right_gauges=False))
        result = assigner.assign(_frame(None, None, left_gauges=True, right_gauges=False))
        assert result.left_stage == "starship"

    def test_unlabelled_sides_default_to_starship(self):
        assigner = _separated()
        assert _stages(assigner.assign(_frame(None, None))) == ("starship", "starship")

    def test_reset_clears_side_memory(self):
        assigner = _separated()
        assigner.assign(_frame("SUPER HEAVY", "STARSHIP"))
        assigner.reset()
        assigner.assign(_frame(sep=True, left_gauges=False))
        assert _stages(assigner.assign(_frame(None, None))) == ("starship", "starship")


def test_label_vehicle():
    for text, vehicle in VEHICLE_LABELS.items():
        assert label_vehicle(text) == vehicle
    assert label_vehicle("'`") is None
    assert label_vehicle(None) is None
