"""Engine Analyzer - detects engine indicators and classifies status.

Uses OpenCV Hough Circle Detection with user-tuned parameters to find
circular engine indicators in frame regions, then classifies each engine's
status based on HSV color sampling within the detected circles.
"""

import math

import cv2
import numpy as np
from dataclasses import dataclass, field

from src.enums import EngineStatus
from src.gpu_detector import GPUCapabilities
from src.models import EngineGroup, ROICircle, ROIRect


@dataclass(frozen=True)
class HoughParams:
    """User-tuned Hough Circle Detection parameters."""

    dp: float = 1.0
    min_dist: int = 7
    param1: int = 50
    param2: int = 10
    min_radius: int = 4
    max_radius: int = 8  # varies by engine type


@dataclass(frozen=True)
class EngineAnalyzerConfig:
    """Configuration for the Engine Analyzer."""

    distance_tolerance: float = 2.0  # pixels — matching reference implementation
    radius_tolerance: float = 3.0  # pixels — max allowed deviation from expected radius
    brightness_threshold: float = 166.0  # V channel in HSV — midpoint between #4e4e4e (V=78) and #ffffff (V=255)
    hough_starship: HoughParams = field(
        default_factory=lambda: HoughParams(
            dp=1.0,
            min_dist=7,
            param1=50,
            param2=10,
            min_radius=4,
            max_radius=20,
        )
    )
    hough_superheavy: HoughParams = field(
        default_factory=lambda: HoughParams(
            dp=1.0,
            min_dist=7,
            param1=50,
            param2=10,
            min_radius=4,
            max_radius=12,
        )
    )


@dataclass
class EngineAnalysisResult:
    """Result of engine analysis for a single frame."""

    engine_statuses: dict[str, EngineStatus]  # keyed by engine id (e.g., "e1")
    detection_accuracy: float  # matched / total expected
    engine_group_bounding_boxes: list[ROIRect]  # for OCR occlusion check


def _get_hough_params(group_id: str, config: EngineAnalyzerConfig) -> HoughParams:
    """Select HoughParams based on engine group type."""
    if "starship" in group_id.lower():
        return config.hough_starship
    return config.hough_superheavy


def _crop_to_bounding_box(
    frame: np.ndarray, bbox: ROIRect
) -> tuple[np.ndarray, float, float]:
    """Crop frame to bounding box region.

    Returns the cropped region and the x/y offsets used for coordinate
    translation back to the full frame.
    """
    h, w = frame.shape[:2]

    # Clamp bounding box to frame dimensions
    x1 = max(0, int(bbox.x))
    y1 = max(0, int(bbox.y))
    x2 = min(w, int(bbox.x + bbox.width))
    y2 = min(h, int(bbox.y + bbox.height))

    cropped = frame[y1:y2, x1:x2]
    return cropped, float(x1), float(y1)


def _detect_circles(
    gray: np.ndarray, params: HoughParams
) -> list[tuple[float, float, float]]:
    """Run Hough Circle Detection and return list of (x, y, radius) tuples."""
    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=params.dp,
        minDist=params.min_dist,
        param1=params.param1,
        param2=params.param2,
        minRadius=params.min_radius,
        maxRadius=params.max_radius,
    )

    if circles is None:
        return []

    # circles shape is (1, N, 3) - squeeze first dimension
    return [(float(c[0]), float(c[1]), float(c[2])) for c in circles[0]]


def _match_circles_to_positions(
    detected_circles: list[tuple[float, float, float]],
    expected_positions: list[tuple[str, float, float, float]],
    distance_tolerance: float,
    radius_tolerance: float,
) -> dict[str, tuple[float, float, float] | None]:
    """Match detected circles to expected SVG positions deterministically.

    Uses a greedy nearest-neighbor matching algorithm with radius validation:
    1. Compute all pairwise distances between detected circles and expected positions
    2. Filter pairs where distance exceeds distance_tolerance OR detected radius
       deviates from expected radius by more than radius_tolerance
    3. Sort valid pairs by a combined score (distance + radius deviation) for
       better matching quality, with expected position index as tiebreaker
    4. Greedily assign best available pairs — each detected circle matches at
       most one expected position and vice versa (strict 1:1 matching)

    Args:
        detected_circles: List of (x, y, radius) for each detected circle
        expected_positions: List of (engine_id, x, y, expected_radius) for expected SVG positions
        distance_tolerance: Maximum Euclidean distance for a valid match
        radius_tolerance: Maximum allowed deviation between detected and expected radius

    Returns:
        Dict mapping engine_id to matched circle (x, y, radius) or None if unmatched
    """
    if not expected_positions:
        return {}

    result: dict[str, tuple[float, float, float] | None] = {
        eid: None for eid, _, _, _ in expected_positions
    }

    if not detected_circles:
        return result

    # Build list of all valid (score, expected_idx, detected_idx) pairs
    pairs: list[tuple[float, int, int]] = []
    for ei, (eid, ex, ey, er) in enumerate(expected_positions):
        for di, (dx, dy, dr) in enumerate(detected_circles):
            dist = math.sqrt((dx - ex) ** 2 + (dy - ey) ** 2)
            radius_diff = abs(dr - er)

            # Both distance and radius must be within tolerance
            if dist > distance_tolerance or radius_diff > radius_tolerance:
                continue

            # Combined score: distance + radius deviation for better ranking
            score = dist + radius_diff
            pairs.append((score, ei, di))

    # Sort by score first, then expected index for determinism on ties
    pairs.sort(key=lambda p: (p[0], p[1], p[2]))

    # Greedy 1:1 assignment — each detected circle maps to exactly one expected
    used_expected: set[int] = set()
    used_detected: set[int] = set()

    for score, ei, di in pairs:
        if ei in used_expected or di in used_detected:
            continue
        eid = expected_positions[ei][0]
        result[eid] = detected_circles[di]
        used_expected.add(ei)
        used_detected.add(di)

    return result


def _classify_engine_color(
    hsv_frame: np.ndarray,
    circle_x: float,
    circle_y: float,
    circle_radius: float,
    config: EngineAnalyzerConfig,
) -> EngineStatus:
    """Classify engine status by sampling mean HSV within the circle area.

    Creates a circular mask and computes mean Value (brightness) within
    the masked area. Classification is based on brightness alone since
    engine indicators on the frame are grayscale:
    - Active engines: #ffffff (V=255, white)
    - Inactive engines: #4e4e4e (V=78, dark gray)

    Classification rule:
    - V > brightness_threshold → ACTIVE
    - V ≤ brightness_threshold → INACTIVE
    """
    h, w = hsv_frame.shape[:2]

    # Create circular mask
    mask = np.zeros((h, w), dtype=np.uint8)
    center = (int(round(circle_x)), int(round(circle_y)))
    radius = max(1, int(round(circle_radius)))
    cv2.circle(mask, center, radius, 255, -1)

    # Sample mean HSV within the circle
    mean_hsv = cv2.mean(hsv_frame, mask=mask)
    # mean_hsv is (H, S, V, _) for 3-channel HSV image
    mean_v = mean_hsv[2]

    if mean_v > config.brightness_threshold:
        return EngineStatus.ACTIVE
    else:
        return EngineStatus.INACTIVE


def _get_all_expected_positions(
    group: EngineGroup, offset_x: float, offset_y: float
) -> list[tuple[str, float, float, float]]:
    """Get all expected engine positions from a group, adjusted for crop offset.

    Args:
        group: The engine group with subgroups containing circles
        offset_x: X offset of the bounding box crop
        offset_y: Y offset of the bounding box crop

    Returns:
        List of (engine_id, x_in_crop, y_in_crop, expected_radius) tuples
    """
    positions: list[tuple[str, float, float, float]] = []
    for subgroup in group.subgroups:
        for circle in subgroup.circles:
            # Translate SVG coordinates to cropped-frame coordinates
            local_x = circle.cx - offset_x
            local_y = circle.cy - offset_y
            positions.append((circle.id, local_x, local_y, circle.r))
    return positions


def analyze_engines(
    frame: np.ndarray,
    engine_groups: list[EngineGroup],
    config: EngineAnalyzerConfig,
    gpu_capabilities: GPUCapabilities,
) -> EngineAnalysisResult:
    """Detect and classify engine statuses using OpenCV HoughCircles.

    Algorithm:
    1. Crop frame to engine group bounding box
    2. Convert cropped region to grayscale
    3. Apply HoughCircles with tuned params (dp=1.0, minDist=7, param1=50, param2=10)
    4. Match detected circles to expected SVG positions within distance_tolerance
    5. For matched circles: convert to HSV, sample color inside circle
       - High brightness (V > threshold) AND high saturation (S > threshold) → ACTIVE
       - Low brightness (V <= threshold) → INACTIVE
    6. Unmatched expected positions → UNDETECTED

    Args:
        frame: BGR numpy array (1920x1080)
        engine_groups: List of engine groups from the ROI configuration
        config: Analyzer configuration with thresholds and Hough params
        gpu_capabilities: GPU capabilities (reserved for future use)

    Returns:
        EngineAnalysisResult with statuses, accuracy, and bounding boxes
    """
    # Handle empty or invalid frame
    if frame is None or frame.size == 0:
        all_statuses: dict[str, EngineStatus] = {}
        for group in engine_groups:
            for subgroup in group.subgroups:
                for circle in subgroup.circles:
                    all_statuses[circle.id] = EngineStatus.UNDETECTED
        return EngineAnalysisResult(
            engine_statuses=all_statuses,
            detection_accuracy=0.0,
            engine_group_bounding_boxes=[],
        )

    engine_statuses: dict[str, EngineStatus] = {}
    active_bounding_boxes: list[ROIRect] = []
    total_expected = 0
    total_matched = 0

    for group in engine_groups:
        bbox = group.bounding_box
        hough_params = _get_hough_params(group.group_id, config)

        # Step 1: Crop frame to engine group bounding box
        cropped, offset_x, offset_y = _crop_to_bounding_box(frame, bbox)

        if cropped.size == 0:
            # Bounding box is outside frame — mark all as undetected
            for subgroup in group.subgroups:
                for circle in subgroup.circles:
                    engine_statuses[circle.id] = EngineStatus.UNDETECTED
                    total_expected += 1
            continue

        # Step 2: Convert cropped region to grayscale
        if len(cropped.shape) == 3:
            gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
        else:
            gray = cropped

        # Step 3: Apply HoughCircles with tuned parameters
        detected_circles = _detect_circles(gray, hough_params)

        # Get all expected positions relative to crop
        expected_positions = _get_all_expected_positions(group, offset_x, offset_y)
        total_expected += len(expected_positions)

        # Step 4: Match detected circles to expected SVG positions
        matches = _match_circles_to_positions(
            detected_circles, expected_positions, config.distance_tolerance, config.radius_tolerance
        )

        # Convert cropped BGR to HSV for color sampling
        if len(cropped.shape) == 3:
            hsv_cropped = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
        else:
            # Grayscale frame — create a synthetic 3-channel HSV
            hsv_cropped = cv2.cvtColor(
                cv2.cvtColor(cropped, cv2.COLOR_GRAY2BGR), cv2.COLOR_BGR2HSV
            )

        # Steps 5 & 6: Classify matched engines, mark unmatched as UNDETECTED
        group_has_detection = False
        for engine_id, matched_circle in matches.items():
            if matched_circle is None:
                engine_statuses[engine_id] = EngineStatus.UNDETECTED
            else:
                cx, cy, cr = matched_circle
                status = _classify_engine_color(hsv_cropped, cx, cy, cr, config)
                engine_statuses[engine_id] = status
                total_matched += 1
                group_has_detection = True

        # Track bounding boxes for groups with detections (for OCR occlusion)
        if group_has_detection:
            active_bounding_boxes.append(bbox)

    # Compute detection accuracy
    detection_accuracy = (
        total_matched / total_expected if total_expected > 0 else 0.0
    )

    return EngineAnalysisResult(
        engine_statuses=engine_statuses,
        detection_accuracy=detection_accuracy,
        engine_group_bounding_boxes=active_bounding_boxes,
    )
