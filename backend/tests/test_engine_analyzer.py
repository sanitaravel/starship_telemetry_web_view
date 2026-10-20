"""Property-based tests for the Engine Analyzer module.

Tests Properties 4, 5, and 6 from the design document:
- Property 4: Engine Color Classification Correctness
- Property 5: Circle Position Matching Determinism
- Property 6: Engine Status Map Completeness

**Validates: Requirements 3.4, 3.6, 3.7, 3.9**
"""

from collections import Counter

import cv2
import numpy as np
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.engine_analyzer import (
    EngineAnalyzerConfig,
    EngineAnalysisResult,
    _classify_engine_color,
    _detect_circles,
    _looks_like_engine_icon,
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
        # The function returns circle tuples, not indices, and detected_circles
        # may contain identical tuples. "No detected circle used twice" is
        # therefore a multiset property: each distinct tuple may appear among
        # the matches at most as many times as it was detected.
        matched_counts = Counter(matched_circles)
        detected_counts = Counter(detected_circles)
        for circle, count in matched_counts.items():
            assert count <= detected_counts[circle], (
                f"circle {circle} matched {count} times but detected "
                f"only {detected_counts[circle]} times"
            )


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


# ─── Engine icon verification and panel gate ──────────────────────────────────

BACKGROUND = 20  # dark panel background (grayscale)
WHITE = (255, 255, 255)  # #ffffff — active
GREY = (78, 78, 78)  # #4e4e4e — inactive

# Checks disabled: used to show a fixture would produce phantoms without them
NO_ICON_CHECKS = EngineAnalyzerConfig(
    max_fill_std=1e9, min_ring_contrast=-1e9, min_panel_fraction=0.0
)

PANEL_BBOX = ROIRect(id="bbox_superheavy", x=100.0, y=100.0, width=120.0, height=120.0)


def _panel_group(n: int = 8, r: float = 6.0) -> EngineGroup:
    """A grid of n engine icons inside PANEL_BBOX, 25 px apart."""
    circles = [
        ROICircle(id=f"e{i}", cx=120.0 + 25.0 * (i % 4), cy=130.0 + 25.0 * (i // 4), r=r)
        for i in range(n)
    ]
    return EngineGroup(
        group_id="superheavy_engines",
        bounding_box=PANEL_BBOX,
        subgroups=[EngineSubgroup(name="all", circles=circles)],
    )


def _panel_frame(group: EngineGroup, colors: list) -> np.ndarray:
    """Dark frame with a filled disc per engine; a None colour leaves it out."""
    frame = np.full((1080, 1920, 3), BACKGROUND, dtype=np.uint8)
    for circle, color in zip(group.subgroups[0].circles, colors):
        if color is not None:
            cv2.circle(frame, (int(circle.cx), int(circle.cy)), int(circle.r), color, -1)
    return frame


class TestLooksLikeEngineIcon:
    """The per-circle check: flat fill that is brighter than its surroundings."""

    def test_uniform_white_disc_passes(self):
        gray = np.full((40, 40), BACKGROUND, dtype=np.uint8)
        cv2.circle(gray, (20, 20), 6, 255, -1)
        assert _looks_like_engine_icon(gray, 20.0, 20.0, 6.0, EngineAnalyzerConfig())

    def test_uniform_grey_disc_passes(self):
        gray = np.full((40, 40), BACKGROUND, dtype=np.uint8)
        cv2.circle(gray, (20, 20), 6, 78, -1)
        assert _looks_like_engine_icon(gray, 20.0, 20.0, 6.0, EngineAnalyzerConfig())

    def test_disc_without_contrast_fails(self):
        """A disc the same brightness as its surroundings is not an icon."""
        gray = np.full((40, 40), 200, dtype=np.uint8)
        assert not _looks_like_engine_icon(gray, 20.0, 20.0, 6.0, EngineAnalyzerConfig())

    def test_high_variance_fill_fails(self):
        """A bright but textured interior (digits, scenery) is not an icon."""
        rng = np.random.default_rng(0)
        gray = np.full((40, 40), BACKGROUND, dtype=np.uint8)
        mask = np.zeros_like(gray)
        cv2.circle(mask, (20, 20), 6, 255, -1)
        noise = rng.integers(60, 256, gray.shape, dtype=np.uint8)
        gray[mask > 0] = noise[mask > 0]
        assert not _looks_like_engine_icon(gray, 20.0, 20.0, 6.0, EngineAnalyzerConfig())

    def test_circle_outside_crop_fails(self):
        gray = np.full((40, 40), BACKGROUND, dtype=np.uint8)
        assert not _looks_like_engine_icon(gray, -50.0, -50.0, 6.0, EngineAnalyzerConfig())

    def test_ring_clipped_by_crop_edge_still_passes(self):
        """A disc touching the crop edge is judged on the part of the ring inside."""
        gray = np.full((40, 40), BACKGROUND, dtype=np.uint8)
        cv2.circle(gray, (7, 20), 6, 255, -1)
        assert _looks_like_engine_icon(gray, 7.0, 20.0, 6.0, EngineAnalyzerConfig())

    @given(
        fill=st.integers(min_value=60, max_value=255),
        background=st.integers(min_value=0, max_value=40),
    )
    @settings(max_examples=30)
    def test_any_flat_disc_brighter_than_background_passes(self, fill, background):
        gray = np.full((40, 40), background, dtype=np.uint8)
        cv2.circle(gray, (20, 20), 6, fill, -1)
        assert _looks_like_engine_icon(gray, 20.0, 20.0, 6.0, EngineAnalyzerConfig())


class TestEnginePanelDetection:
    """analyze_engines with the icon check and the panel gate."""

    def test_visible_panel_is_fully_detected_and_classified(self):
        group = _panel_group(8)
        colors = [WHITE, GREY] * 4
        result = analyze_engines(
            _panel_frame(group, colors), [group], EngineAnalyzerConfig(), CPU_CAPABILITIES
        )

        expected = [EngineStatus.ACTIVE, EngineStatus.INACTIVE] * 4
        assert [result.engine_statuses[f"e{i}"] for i in range(8)] == expected
        assert result.detection_accuracy == 1.0
        assert result.engine_group_bounding_boxes == [PANEL_BBOX]

    def test_textured_discs_are_undetected(self):
        """Noisy discs at the expected positions are found by Hough but rejected."""
        group = _panel_group(8)
        frame = np.full((1080, 1920, 3), BACKGROUND, dtype=np.uint8)
        rng = np.random.default_rng(0)
        noise = rng.integers(40, 256, frame.shape, dtype=np.uint8)
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        for circle in group.subgroups[0].circles:
            cv2.circle(mask, (int(circle.cx), int(circle.cy)), int(circle.r), 255, -1)
        frame[mask > 0] = noise[mask > 0]

        # Without the checks these would be phantom detections
        assert analyze_engines(frame, [group], NO_ICON_CHECKS, CPU_CAPABILITIES).detection_accuracy > 0

        result = analyze_engines(frame, [group], EngineAnalyzerConfig(), CPU_CAPABILITIES)
        assert all(s == EngineStatus.UNDETECTED for s in result.engine_statuses.values())
        assert result.detection_accuracy == 0.0
        assert result.engine_group_bounding_boxes == []

    def test_text_in_box_is_undetected(self):
        """Digits where the panel would be (panel off screen) produce no engines.

        Expected positions are placed exactly on the circles Hough finds in
        the digits, the worst case for phantom matches.
        """
        frame = np.full((1080, 1920, 3), BACKGROUND, dtype=np.uint8)
        cv2.putText(frame, "8060", (105, 165), cv2.FONT_HERSHEY_SIMPLEX, 1.6, WHITE, 3)
        cv2.putText(frame, "0980", (105, 215), cv2.FONT_HERSHEY_SIMPLEX, 1.6, WHITE, 3)

        config = EngineAnalyzerConfig()
        crop = cv2.cvtColor(frame[100:220, 100:220], cv2.COLOR_BGR2GRAY)
        phantoms = _detect_circles(crop, config.hough_superheavy)
        assert phantoms, "fixture should give Hough something to find"

        circles = [
            ROICircle(id=f"e{i}", cx=100.0 + x, cy=100.0 + y, r=float(round(r)))
            for i, (x, y, r) in enumerate(phantoms)
        ]
        group = EngineGroup(
            group_id="superheavy_engines",
            bounding_box=PANEL_BBOX,
            subgroups=[EngineSubgroup(name="all", circles=circles)],
        )

        assert analyze_engines(frame, [group], NO_ICON_CHECKS, CPU_CAPABILITIES).detection_accuracy > 0

        result = analyze_engines(frame, [group], config, CPU_CAPABILITIES)
        assert all(s == EngineStatus.UNDETECTED for s in result.engine_statuses.values())
        assert result.detection_accuracy == 0.0
        assert result.engine_group_bounding_boxes == []

    def test_panel_below_min_fraction_is_undetected(self):
        """4 of 8 icons (50% < 60%): the whole group is UNDETECTED."""
        group = _panel_group(8)
        colors = [WHITE, GREY, WHITE, GREY, None, None, None, None]
        result = analyze_engines(
            _panel_frame(group, colors), [group], EngineAnalyzerConfig(), CPU_CAPABILITIES
        )

        assert all(s == EngineStatus.UNDETECTED for s in result.engine_statuses.values())
        assert result.detection_accuracy == 0.0
        assert result.engine_group_bounding_boxes == []

    def test_panel_at_min_fraction_keeps_verified_engines(self):
        """5 of 8 icons (62.5% >= 60%): present icons classified, rest UNDETECTED."""
        group = _panel_group(8)
        colors = [WHITE, GREY, WHITE, GREY, WHITE, None, None, None]
        result = analyze_engines(
            _panel_frame(group, colors), [group], EngineAnalyzerConfig(), CPU_CAPABILITIES
        )

        expected = [
            EngineStatus.ACTIVE, EngineStatus.INACTIVE, EngineStatus.ACTIVE,
            EngineStatus.INACTIVE, EngineStatus.ACTIVE,
            EngineStatus.UNDETECTED, EngineStatus.UNDETECTED, EngineStatus.UNDETECTED,
        ]
        assert [result.engine_statuses[f"e{i}"] for i in range(8)] == expected
        assert result.detection_accuracy == 5 / 8
        assert result.engine_group_bounding_boxes == [PANEL_BBOX]

    def test_offset_match_on_small_icon_is_verified(self, monkeypatch):
        """Regression (MET 60, sh_e33): a Hough match ~1.6 px off a 5.5 px icon.

        At the matched centre the 0.7*r inner disc reaches the icon's dark
        edge and fails; the check must run at the expected template centre.
        """
        import src.engine_analyzer as engine_analyzer

        r = 5.5
        circles = [
            ROICircle(id=f"e{i}", cx=120.0 + 25.0 * i, cy=130.0, r=r) for i in range(4)
        ]
        group = EngineGroup(
            group_id="superheavy_engines",
            bounding_box=PANEL_BBOX,
            subgroups=[EngineSubgroup(name="all", circles=circles)],
        )

        # Anti-aliased icon with a soft dark rim, like the webcast HUD
        frame = np.full((1080, 1920, 3), BACKGROUND, dtype=np.uint8)
        yy, xx = np.mgrid[0:1080, 0:1920]
        for c in circles:
            dist = np.hypot(xx - c.cx, yy - c.cy)
            alpha = np.clip(r - dist, 0.0, 1.0)  # 1 inside, ramps to 0 over the last px
            rim = (dist > r - 1.0) & (dist <= r)
            value = BACKGROUND + alpha * (255 - BACKGROUND)
            value[rim] = BACKGROUND + 0.3 * alpha[rim] * (255 - BACKGROUND)
            mask = alpha > 0
            frame[mask] = value[mask, None].astype(np.uint8)

        # Hough reports every centre 1.1 px right and 1.1 px down (~1.56 px off)
        offset = 1.1
        crop_circles = [
            (c.cx - PANEL_BBOX.x + offset, c.cy - PANEL_BBOX.y + offset, r) for c in circles
        ]
        monkeypatch.setattr(engine_analyzer, "_detect_circles", lambda gray, params: crop_circles)

        config = EngineAnalyzerConfig()
        crop = cv2.cvtColor(frame[100:220, 100:220], cv2.COLOR_BGR2GRAY)
        # The fixture reproduces the failure mode at the matched centre
        mx, my, _ = crop_circles[0]
        assert not _looks_like_engine_icon(crop, mx, my, r, config)

        result = analyze_engines(frame, [group], config, CPU_CAPABILITIES)
        assert all(result.engine_statuses[f"e{i}"] == EngineStatus.ACTIVE for i in range(4))
        assert result.detection_accuracy == 1.0

    def test_gate_is_per_group(self):
        """A missing panel in one group does not suppress the other group."""
        visible = _panel_group(8)
        hidden = EngineGroup(
            group_id="starship_engines",
            bounding_box=ROIRect(id="bbox_starship", x=400.0, y=100.0, width=120.0, height=120.0),
            subgroups=[EngineSubgroup(name="all", circles=[
                ROICircle(id=f"s{i}", cx=420.0 + 25.0 * i, cy=130.0, r=6.0) for i in range(4)
            ])],
        )
        frame = _panel_frame(visible, [WHITE] * 8)
        result = analyze_engines(frame, [visible, hidden], EngineAnalyzerConfig(), CPU_CAPABILITIES)

        assert all(result.engine_statuses[f"e{i}"] == EngineStatus.ACTIVE for i in range(8))
        assert all(result.engine_statuses[f"s{i}"] == EngineStatus.UNDETECTED for i in range(4))
        assert result.detection_accuracy == 8 / 12
        assert result.engine_group_bounding_boxes == [PANEL_BBOX]
