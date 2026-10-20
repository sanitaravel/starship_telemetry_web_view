"""Engine Analyzer - detects engine indicators and classifies status.

Engine icons sit at fixed HUD positions taken from the SVG templates. Each
template position is checked for a flat, contrasting engine icon and, if
one is there, classified by the brightness sampled inside it. A group whose
panel is not on screen (too few icons found) is reported as UNDETECTED.
"""

import math

import cv2
import numpy as np
from dataclasses import dataclass

from src.enums import EngineStatus
from src.gpu_detector import GPUCapabilities
from src.models import EngineGroup, ROIRect


@dataclass(frozen=True)
class EngineAnalyzerConfig:
    """Configuration for the Engine Analyzer."""

    brightness_threshold: float = 166.0  # V channel in HSV — midpoint between #4e4e4e (V=78) and #ffffff (V=255)
    max_fill_std: float = 20.0  # grayscale — max std of pixels inside 0.7*r; icons are flat discs (real <= 17)
    min_ring_contrast: float = 10.0  # grayscale — min (inner mean - ring mean); icons stand out from the panel (real >= 17)
    min_panel_fraction: float = 0.6  # min share of a group's icons found for the panel to count as on screen


@dataclass
class EngineAnalysisResult:
    """Result of engine analysis for a single frame."""

    engine_statuses: dict[str, EngineStatus]  # keyed by engine id (e.g., "e1")
    detection_accuracy: float  # detected / total expected
    engine_group_bounding_boxes: list[ROIRect]  # for OCR occlusion check


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


def _classify_engine_color(
    hsv_frame: np.ndarray,
    circle_x: float,
    circle_y: float,
    circle_radius: float,
    config: EngineAnalyzerConfig,
) -> EngineStatus:
    """Classify engine status by sampling mean HSV within the circle area.

    Builds a circular mask on a small window around the circle and computes
    mean Value (brightness) within it. Classification is based on brightness
    alone since engine indicators on the frame are grayscale:
    - Active engines: #ffffff (V=255, white)
    - Inactive engines: #4e4e4e (V=78, dark gray)

    Classification rule:
    - V > brightness_threshold → ACTIVE
    - V ≤ brightness_threshold → INACTIVE
    """
    h, w = hsv_frame.shape[:2]
    center_x = int(round(circle_x))
    center_y = int(round(circle_y))
    radius = max(1, int(round(circle_radius)))

    # Local window around the circle, clamped to the frame
    x0 = max(0, center_x - radius)
    y0 = max(0, center_y - radius)
    x1 = min(w, center_x + radius + 1)
    y1 = min(h, center_y + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return EngineStatus.INACTIVE

    # Circular mask in window coordinates
    window = hsv_frame[y0:y1, x0:x1]
    mask = np.zeros(window.shape[:2], dtype=np.uint8)
    cv2.circle(mask, (center_x - x0, center_y - y0), radius, 255, -1)

    # Sample mean HSV within the circle
    mean_hsv = cv2.mean(window, mask=mask)
    # mean_hsv is (H, S, V, _) for 3-channel HSV image
    mean_v = mean_hsv[2]

    if mean_v > config.brightness_threshold:
        return EngineStatus.ACTIVE
    else:
        return EngineStatus.INACTIVE


def _looks_like_engine_icon(
    gray: np.ndarray,
    circle_x: float,
    circle_y: float,
    circle_radius: float,
    config: EngineAnalyzerConfig,
) -> bool:
    """Check that a template position holds a HUD engine icon.

    Engine icons are flat-filled discs (white or dark grey) that are brighter
    than the panel immediately around them. When the engine panel is not on
    screen the position shows digits, text or scenery instead; those have
    textured interiors or no contrast with their surroundings.

    Works on a small window around the circle only:
    - inner: pixels within 0.7 * radius of the centre
    - ring: annulus radius + 1.5 .. radius + 3.5 px (clipped to the crop)

    Passes when inner std <= max_fill_std and
    (inner mean - ring mean) >= min_ring_contrast. Empty inner or ring → fails.
    """
    h, w = gray.shape[:2]
    inner_r = 0.7 * circle_radius
    ring_r1 = circle_radius + 1.5
    ring_r2 = circle_radius + 3.5

    # Local window covering the outer edge of the ring, clamped to the crop
    x0 = max(0, int(math.floor(circle_x - ring_r2)))
    y0 = max(0, int(math.floor(circle_y - ring_r2)))
    x1 = min(w, int(math.ceil(circle_x + ring_r2)) + 1)
    y1 = min(h, int(math.ceil(circle_y + ring_r2)) + 1)
    if x0 >= x1 or y0 >= y1:
        return False

    window = gray[y0:y1, x0:x1].astype(np.float32)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dist = np.hypot(xx - circle_x, yy - circle_y)

    inner = window[dist <= inner_r]
    ring = window[(dist >= ring_r1) & (dist <= ring_r2)]
    if inner.size == 0 or ring.size == 0:
        return False

    if float(inner.std()) > config.max_fill_std:
        return False
    return float(inner.mean()) - float(ring.mean()) >= config.min_ring_contrast


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
    """Detect and classify engine statuses at the template positions.

    Icon positions in the HUD are fixed, so each expected position is checked
    directly rather than searched for.

    Algorithm:
    1. Crop frame to engine group bounding box
    2. Convert cropped region to grayscale and HSV
    3. At every expected position (template centre and radius), check for an
       engine icon: flat fill, brighter than its surrounding ring
    4. Panel gate: if fewer than min_panel_fraction of the group's icons are
       found, the panel is treated as off screen — every engine in the group
       is UNDETECTED, it adds 0 to accuracy and its bounding box is not
       reported
    5. For found icons: sample brightness inside the template circle
       - High brightness (V > threshold) → ACTIVE
       - Low brightness (V <= threshold) → INACTIVE
    6. Positions without an icon → UNDETECTED

    Args:
        frame: BGR numpy array (1920x1080)
        engine_groups: List of engine groups from the ROI configuration
        config: Analyzer configuration with icon and brightness thresholds
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
    total_detected = 0

    for group in engine_groups:
        bbox = group.bounding_box

        # Step 1: Crop frame to engine group bounding box
        cropped, offset_x, offset_y = _crop_to_bounding_box(frame, bbox)

        if cropped.size == 0:
            # Bounding box is outside frame — mark all as undetected
            for subgroup in group.subgroups:
                for circle in subgroup.circles:
                    engine_statuses[circle.id] = EngineStatus.UNDETECTED
                    total_expected += 1
            continue

        # Step 2: Convert cropped region to grayscale and HSV
        if len(cropped.shape) == 3:
            gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
            hsv_cropped = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
        else:
            gray = cropped
            # Grayscale frame — create a synthetic 3-channel HSV
            hsv_cropped = cv2.cvtColor(
                cv2.cvtColor(cropped, cv2.COLOR_GRAY2BGR), cv2.COLOR_BGR2HSV
            )

        # Get all expected positions relative to crop
        expected_positions = _get_all_expected_positions(group, offset_x, offset_y)
        total_expected += len(expected_positions)

        # Step 3: Check every template position for an engine icon
        found = [
            (engine_id, x, y, r)
            for engine_id, x, y, r in expected_positions
            if _looks_like_engine_icon(gray, x, y, r, config)
        ]

        # Step 4: Panel gate — too few icons means the panel is not on screen
        if len(found) < config.min_panel_fraction * len(expected_positions):
            for engine_id, _, _, _ in expected_positions:
                engine_statuses[engine_id] = EngineStatus.UNDETECTED
            continue

        # Steps 5 & 6: Classify found icons, mark the rest as UNDETECTED
        for engine_id, _, _, _ in expected_positions:
            engine_statuses[engine_id] = EngineStatus.UNDETECTED
        for engine_id, x, y, r in found:
            engine_statuses[engine_id] = _classify_engine_color(hsv_cropped, x, y, r, config)
            total_detected += 1

        # Track bounding boxes for groups with detections (for OCR occlusion)
        if found:
            active_bounding_boxes.append(bbox)

    # Compute detection accuracy
    detection_accuracy = (
        total_detected / total_expected if total_expected > 0 else 0.0
    )

    return EngineAnalysisResult(
        engine_statuses=engine_statuses,
        detection_accuracy=detection_accuracy,
        engine_group_bounding_boxes=active_bounding_boxes,
    )
