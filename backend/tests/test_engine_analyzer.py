"""Property-based tests for the Engine Analyzer module.

Tests Properties 4, 5, and 6 from the design document:
- Property 4: Engine Color Classification Correctness
- Property 5: Circle Position Matching Determinism
- Property 6: Engine Status Map Completeness

**Validates: Requirements 3.4, 3.6, 3.7, 3.9**
"""

import numpy as np
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.engine_analyzer import (
    EngineAnalyzerConfig,
    EngineAnalysisResult,
    _classify_engine_color,
    _match_circles_to_positions,
    analyze_engines,
)
from src.enums import EngineStatus
from src.gpu_detector import GPUCapabilities, AccelerationBackend
from src.models import EngineGroup, EngineSubgroup, ROICircle, ROIRect


# ─── Strategies ───────────────────────────────────────────────────────────────

def hsv_value_strategy():
    """Strategy for HSV Value channel (0-255)."""
    return st.floats(min_value=0.0, max_value=255.0, allow_nan=False, allow_infinity=False)


def hsv_saturation_strategy():
    """Strategy for HSV Saturation channel (0-255)."""
    return st.floats(min_value=0.0, max_value=255.0, allow_nan=False, allow_infinity=False)


def circle_position_strategy():
    """Strategy for generating circle positions within a reasonable frame area."""
    return st.tuples(
        st.floats(min_value=10.0, max_value=1900.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=10.0, max_value=1060.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=3.0, max_value=15.0, allow_nan=False, allow_infinity=False),
    )


def expected_position_strategy():
    """Strategy for generating expected SVG positions with unique engine IDs and expected radius."""
    return st.tuples(
        st.text(
            alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_"),
            min_size=2,
            max_size=6,
        ),
        st.floats(min_value=10.0, max_value=1900.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=10.0, max_value=1060.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=3.0, max_value=15.0, allow_nan=False, allow_infinity=False),
    )


def unique_expected_positions_strategy(min_size=1, max_size=10):
    """Strategy for lists of expected positions with unique engine IDs."""
    return st.lists(
        expected_position_strategy(),
        min_size=min_size,
        max_size=max_size,
    ).filter(lambda positions: len(set(p[0] for p in positions)) == len(positions))


def engine_circles_strategy(n: int, bbox_x: float, bbox_y: float, bbox_w: float, bbox_h: float):
    """Strategy for generating n engine circles within a bounding box."""
    return st.lists(
        st.tuples(
            st.floats(min_value=bbox_x + 5, max_value=bbox_x + bbox_w - 5, allow_nan=False, allow_infinity=False),
            st.floats(min_value=bbox_y + 5, max_value=bbox_y + bbox_h - 5, allow_nan=False, allow_infinity=False),
            st.floats(min_value=3.0, max_value=8.0, allow_nan=False, allow_infinity=False),
        ),
        min_size=n,
        max_size=n,
    )


# ─── CPU fallback fixture ─────────────────────────────────────────────────────

CPU_CAPABILITIES = GPUCapabilities(
    backend=AccelerationBackend.CPU,
    device_name=None,
    cuda_version=None,
    gpu_available=False,
)


# ─── Property 4: Engine Color Classification Correctness ──────────────────────
# **Validates: Requirements 3.4, 3.6**

class TestEngineColorClassification:
    """Property 4: Engine Color Classification Correctness.

    Engine indicators on the frame are grayscale:
    - Active: #ffffff (V=255, white)
    - Inactive: #4e4e4e (V=78, dark gray)

    Classification rule: V > brightness_threshold → ACTIVE, else INACTIVE.
    """

    @given(
        hue=st.integers(min_value=0, max_value=179),
        saturation=st.integers(min_value=0, max_value=255),
        value=st.integers(min_value=0, max_value=255),
        brightness_threshold=st.floats(min_value=1.0, max_value=254.0, allow_nan=False, allow_infinity=False),
    )
    def test_active_when_high_brightness(
        self, hue, saturation, value, brightness_threshold
    ):
        """Engine classified ACTIVE when V > brightness_threshold."""
        assume(value > brightness_threshold)

        # Create a uniform HSV image large enough for a circle
        size = 30
        hsv_frame = np.full((size, size, 3), [hue, saturation, value], dtype=np.uint8)

        config = EngineAnalyzerConfig(
            brightness_threshold=brightness_threshold,
        )

        result = _classify_engine_color(
            hsv_frame,
            circle_x=size / 2,
            circle_y=size / 2,
            circle_radius=5.0,
            config=config,
        )

        assert result == EngineStatus.ACTIVE

    @given(
        hue=st.integers(min_value=0, max_value=179),
        saturation=st.integers(min_value=0, max_value=255),
        value=st.integers(min_value=0, max_value=255),
        brightness_threshold=st.floats(min_value=1.0, max_value=254.0, allow_nan=False, allow_infinity=False),
    )
    def test_inactive_when_low_brightness(
        self, hue, saturation, value, brightness_threshold
    ):
        """Engine classified INACTIVE when V ≤ brightness_threshold."""
        assume(value <= brightness_threshold)

        size = 30
        hsv_frame = np.full((size, size, 3), [hue, saturation, value], dtype=np.uint8)

        config = EngineAnalyzerConfig(
            brightness_threshold=brightness_threshold,
        )

        result = _classify_engine_color(
            hsv_frame,
            circle_x=size / 2,
            circle_y=size / 2,
            circle_radius=5.0,
            config=config,
        )

        assert result == EngineStatus.INACTIVE

    def test_ffffff_is_active(self):
        """#ffffff (pure white, V=255) is classified as ACTIVE with default config."""
        size = 30
        # #ffffff in HSV is (0, 0, 255)
        hsv_frame = np.full((size, size, 3), [0, 0, 255], dtype=np.uint8)
        config = EngineAnalyzerConfig()

        result = _classify_engine_color(
            hsv_frame, circle_x=size / 2, circle_y=size / 2,
            circle_radius=5.0, config=config,
        )
        assert result == EngineStatus.ACTIVE

    def test_4e4e4e_is_inactive(self):
        """#4e4e4e (dark gray, V=78) is classified as INACTIVE with default config."""
        size = 30
        # #4e4e4e in HSV is (0, 0, 78)
        hsv_frame = np.full((size, size, 3), [0, 0, 78], dtype=np.uint8)
        config = EngineAnalyzerConfig()

        result = _classify_engine_color(
            hsv_frame, circle_x=size / 2, circle_y=size / 2,
            circle_radius=5.0, config=config,
        )
        assert result == EngineStatus.INACTIVE


# ─── Property 5: Circle Position Matching Determinism ─────────────────────────
# **Validates: Requirements 3.7**

class TestCirclePositionMatchingDeterminism:
    """Property 5: Circle Position Matching Determinism.

    For any set of detected circle positions and expected SVG circle positions
    with a given distance tolerance, the matching algorithm SHALL be deterministic —
    running it twice on the same inputs produces the same matched pairs.
    """

    @given(
        detected_circles=st.lists(
            circle_position_strategy(),
            min_size=0,
            max_size=15,
        ),
        expected_positions=unique_expected_positions_strategy(min_size=1, max_size=15),
        distance_tolerance=st.floats(min_value=1.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    )
    def test_matching_is_deterministic(self, detected_circles, expected_positions, distance_tolerance):
        """Running _match_circles_to_positions twice on same inputs produces identical results."""
        radius_tolerance = 50.0  # large tolerance so radius doesn't filter in determinism test
        result1 = _match_circles_to_positions(
            detected_circles, expected_positions, distance_tolerance, radius_tolerance
        )
        result2 = _match_circles_to_positions(
            detected_circles, expected_positions, distance_tolerance, radius_tolerance
        )

        assert result1 == result2

    @given(
        detected_circles=st.lists(
            circle_position_strategy(),
            min_size=0,
            max_size=15,
        ),
        expected_positions=unique_expected_positions_strategy(min_size=1, max_size=15),
        distance_tolerance=st.floats(min_value=1.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    )
    def test_matching_returns_all_expected_keys(self, detected_circles, expected_positions, distance_tolerance):
        """Result always contains an entry for every expected engine ID."""
        radius_tolerance = 50.0  # large tolerance so radius doesn't filter in key completeness test
        result = _match_circles_to_positions(
            detected_circles, expected_positions, distance_tolerance, radius_tolerance
        )

        expected_ids = {eid for eid, _, _, _ in expected_positions}
        assert set(result.keys()) == expected_ids

    @given(
        detected_circles=st.lists(
            circle_position_strategy(),
            min_size=0,
            max_size=15,
        ),
        expected_positions=unique_expected_positions_strategy(min_size=1, max_size=15),
        distance_tolerance=st.floats(min_value=1.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    )
    def test_no_detected_circle_matched_twice(self, detected_circles, expected_positions, distance_tolerance):
        """Each detected circle is matched to at most one expected position."""
        radius_tolerance = 50.0  # large tolerance so radius doesn't filter in uniqueness test
        result = _match_circles_to_positions(
            detected_circles, expected_positions, distance_tolerance, radius_tolerance
        )

        matched_circles = [v for v in result.values() if v is not None]
        # Each matched circle should be unique (no duplicates)
        assert len(matched_circles) == len(set(matched_circles))


# ─── Property 6: Engine Status Map Completeness ───────────────────────────────
# **Validates: Requirements 3.9**

class TestEngineStatusMapCompleteness:
    """Property 6: Engine Status Map Completeness.

    For any valid frame and ROI configuration, the Engine Analyzer SHALL produce
    a status map containing exactly one entry for every expected engine ID,
    with each entry having a valid EngineStatus value.
    """

    @given(
        num_subgroups=st.integers(min_value=1, max_value=3),
        engines_per_subgroup=st.integers(min_value=1, max_value=11),
    )
    @settings(max_examples=30)
    def test_status_map_contains_all_engine_ids(self, num_subgroups, engines_per_subgroup):
        """Status map has exactly one entry per expected engine ID."""
        # Build a synthetic engine group with known engine IDs
        bbox = ROIRect(id="bbox_starship", x=100.0, y=100.0, width=200.0, height=200.0)

        subgroups = []
        all_engine_ids = []
        for sg_idx in range(num_subgroups):
            circles = []
            for eng_idx in range(engines_per_subgroup):
                engine_id = f"e{sg_idx}_{eng_idx}"
                all_engine_ids.append(engine_id)
                circles.append(ROICircle(
                    id=engine_id,
                    cx=100.0 + 20.0 * (eng_idx + 1),
                    cy=100.0 + 50.0 * (sg_idx + 1),
                    r=5.0,
                ))
            subgroups.append(EngineSubgroup(name=f"subgroup_{sg_idx}", circles=circles))

        engine_group = EngineGroup(
            group_id="starship_engines",
            bounding_box=bbox,
            subgroups=subgroups,
        )

        # Create a solid-color BGR frame (1920x1080)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        config = EngineAnalyzerConfig()

        result = analyze_engines(frame, [engine_group], config, CPU_CAPABILITIES)

        # Verify: status map has exactly one entry for every expected engine ID
        assert set(result.engine_statuses.keys()) == set(all_engine_ids)
        assert len(result.engine_statuses) == len(all_engine_ids)

    @given(
        num_subgroups=st.integers(min_value=1, max_value=3),
        engines_per_subgroup=st.integers(min_value=1, max_value=11),
    )
    @settings(max_examples=30)
    def test_all_statuses_are_valid_engine_status(self, num_subgroups, engines_per_subgroup):
        """Every entry in the status map has a valid EngineStatus value."""
        bbox = ROIRect(id="bbox_starship", x=100.0, y=100.0, width=200.0, height=200.0)

        subgroups = []
        for sg_idx in range(num_subgroups):
            circles = []
            for eng_idx in range(engines_per_subgroup):
                engine_id = f"e{sg_idx}_{eng_idx}"
                circles.append(ROICircle(
                    id=engine_id,
                    cx=100.0 + 20.0 * (eng_idx + 1),
                    cy=100.0 + 50.0 * (sg_idx + 1),
                    r=5.0,
                ))
            subgroups.append(EngineSubgroup(name=f"subgroup_{sg_idx}", circles=circles))

        engine_group = EngineGroup(
            group_id="starship_engines",
            bounding_box=bbox,
            subgroups=subgroups,
        )

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        config = EngineAnalyzerConfig()

        result = analyze_engines(frame, [engine_group], config, CPU_CAPABILITIES)

        valid_statuses = {EngineStatus.ACTIVE, EngineStatus.INACTIVE, EngineStatus.UNDETECTED}
        for engine_id, status in result.engine_statuses.items():
            assert status in valid_statuses, (
                f"Engine {engine_id} has invalid status: {status}"
            )

    def test_starship_6_engines_completeness(self):
        """Starship configuration produces exactly 6 engine statuses."""
        bbox = ROIRect(id="bbox_starship", x=800.0, y=600.0, width=300.0, height=200.0)
        circles = [
            ROICircle(id=f"se{i}", cx=800.0 + 30.0 * (i + 1), cy=700.0, r=5.0)
            for i in range(6)
        ]
        engine_group = EngineGroup(
            group_id="starship_engines",
            bounding_box=bbox,
            subgroups=[EngineSubgroup(name="all", circles=circles)],
        )

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        config = EngineAnalyzerConfig()

        result = analyze_engines(frame, [engine_group], config, CPU_CAPABILITIES)

        assert len(result.engine_statuses) == 6
        assert all(eid in result.engine_statuses for eid in [f"se{i}" for i in range(6)])

    def test_superheavy_33_engines_completeness(self):
        """Super Heavy configuration produces exactly 33 engine statuses."""
        bbox = ROIRect(id="bbox_superheavy", x=100.0, y=100.0, width=400.0, height=400.0)

        # 3 subgroups: inner(3), middle(10), outer(20)
        inner_circles = [
            ROICircle(id=f"sh_inner_{i}", cx=250.0 + 20.0 * i, cy=250.0, r=5.0)
            for i in range(3)
        ]
        middle_circles = [
            ROICircle(id=f"sh_middle_{i}", cx=200.0 + 30.0 * (i % 5), cy=200.0 + 30.0 * (i // 5), r=5.0)
            for i in range(10)
        ]
        outer_circles = [
            ROICircle(id=f"sh_outer_{i}", cx=150.0 + 20.0 * (i % 7), cy=150.0 + 20.0 * (i // 7), r=5.0)
            for i in range(20)
        ]

        engine_group = EngineGroup(
            group_id="superheavy_engines",
            bounding_box=bbox,
            subgroups=[
                EngineSubgroup(name="inner", circles=inner_circles),
                EngineSubgroup(name="middle", circles=middle_circles),
                EngineSubgroup(name="outer", circles=outer_circles),
            ],
        )

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        config = EngineAnalyzerConfig()

        result = analyze_engines(frame, [engine_group], config, CPU_CAPABILITIES)

        assert len(result.engine_statuses) == 33
        valid_statuses = {EngineStatus.ACTIVE, EngineStatus.INACTIVE, EngineStatus.UNDETECTED}
        for status in result.engine_statuses.values():
            assert status in valid_statuses

    @given(
        num_subgroups=st.integers(min_value=1, max_value=3),
        engines_per_subgroup=st.integers(min_value=1, max_value=11),
    )
    @settings(max_examples=30)
    def test_detection_accuracy_in_valid_range(self, num_subgroups, engines_per_subgroup):
        """Detection accuracy is always between 0.0 and 1.0."""
        bbox = ROIRect(id="bbox_starship", x=100.0, y=100.0, width=200.0, height=200.0)

        subgroups = []
        for sg_idx in range(num_subgroups):
            circles = []
            for eng_idx in range(engines_per_subgroup):
                engine_id = f"e{sg_idx}_{eng_idx}"
                circles.append(ROICircle(
                    id=engine_id,
                    cx=100.0 + 20.0 * (eng_idx + 1),
                    cy=100.0 + 50.0 * (sg_idx + 1),
                    r=5.0,
                ))
            subgroups.append(EngineSubgroup(name=f"subgroup_{sg_idx}", circles=circles))

        engine_group = EngineGroup(
            group_id="starship_engines",
            bounding_box=bbox,
            subgroups=subgroups,
        )

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        config = EngineAnalyzerConfig()

        result = analyze_engines(frame, [engine_group], config, CPU_CAPABILITIES)

        assert 0.0 <= result.detection_accuracy <= 1.0
