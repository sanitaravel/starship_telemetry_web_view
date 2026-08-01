"""OCR Engine - extracts text values from frame regions using EasyOCR.

Uses EasyOCR with GPU acceleration (when available) to read text values
(time, speed, altitude, stage labels) from non-occluded ROI regions.
Regions overlapping with detected engine bounding boxes are skipped and
marked as occluded.
"""

import re
from dataclasses import dataclass

import easyocr
import numpy as np

from src.enums import OCRFieldStatus
from src.gpu_detector import GPUCapabilities
from src.models import ROIRect


# Confidence threshold below which OCR results are marked UNAVAILABLE
_CONFIDENCE_THRESHOLD = 0.3

# Time format regex: T+HH:MM:SS or T-HH:MM:SS (flexible with possible OCR noise)
_TIME_PATTERN = re.compile(r"[Tt][+\-−]?\s*(\d{1,2}):(\d{2}):(\d{2})")

# Numeric value regex: optional sign, digits with optional decimal point
_NUMERIC_PATTERN = re.compile(r"[+\-−]?\s*(\d+(?:[.,]\d+)?)")


@dataclass
class OCRFieldResult:
    """Result of OCR extraction for a single text field."""

    status: OCRFieldStatus
    raw_text: str | None = None
    parsed_value: float | str | None = None


@dataclass
class OCRResult:
    """Aggregated OCR results for all text regions in a frame."""

    time: OCRFieldResult
    speed_l: OCRFieldResult
    speed_l_unit: OCRFieldResult
    speed_r: OCRFieldResult
    speed_r_unit: OCRFieldResult
    altitude_l: OCRFieldResult
    altitude_l_unit: OCRFieldResult
    altitude_r: OCRFieldResult
    altitude_r_unit: OCRFieldResult
    stage_l: OCRFieldResult
    stage_r: OCRFieldResult
    stage_sep_text: OCRFieldResult


def _rects_intersect(a: ROIRect, b: ROIRect) -> bool:
    """Check if two axis-aligned bounding boxes overlap (AABB intersection).

    Returns True if rectangles a and b share any area.
    """
    a_left = a.x
    a_right = a.x + a.width
    a_top = a.y
    a_bottom = a.y + a.height

    b_left = b.x
    b_right = b.x + b.width
    b_top = b.y
    b_bottom = b.y + b.height

    # No overlap if one is entirely to the left/right/above/below the other
    if a_right <= b_left or b_right <= a_left:
        return False
    if a_bottom <= b_top or b_bottom <= a_top:
        return False

    return True


def _is_occluded_by_engines(
    text_roi: ROIRect, engine_bounding_boxes: list[ROIRect]
) -> bool:
    """Check if a text ROI intersects any engine bounding box."""
    for engine_bbox in engine_bounding_boxes:
        if _rects_intersect(text_roi, engine_bbox):
            return True
    return False


def _crop_frame_to_roi(frame: np.ndarray, roi: ROIRect) -> np.ndarray:
    """Crop the frame to the given ROI rectangle.

    Clamps coordinates to frame boundaries to avoid out-of-bounds access.
    """
    h, w = frame.shape[:2]

    x1 = max(0, int(roi.x))
    y1 = max(0, int(roi.y))
    x2 = min(w, int(roi.x + roi.width))
    y2 = min(h, int(roi.y + roi.height))

    return frame[y1:y2, x1:x2]


def _parse_time_value(text: str) -> str | None:
    """Parse mission elapsed time from OCR text.

    Expected format: T+HH:MM:SS or T-HH:MM:SS
    Returns normalized time string or None if parsing fails.
    """
    match = _TIME_PATTERN.search(text)
    if not match:
        return None

    hours = match.group(1).zfill(2)
    minutes = match.group(2)
    seconds = match.group(3)

    # Determine sign: look for minus/dash before the digits
    sign_char = "+"
    # Find where T is and check the character after it
    t_idx = text.lower().find("t")
    if t_idx >= 0 and t_idx + 1 < len(text):
        after_t = text[t_idx + 1]
        if after_t in ("-", "−"):
            sign_char = "-"

    return f"T{sign_char}{hours}:{minutes}:{seconds}"


def _parse_numeric_value(text: str) -> float | None:
    """Parse a numeric value (speed or altitude) from OCR text.

    Handles integers and decimals. Returns None if no numeric value found.
    """
    match = _NUMERIC_PATTERN.search(text)
    if not match:
        return None

    # Replace comma with dot for decimal parsing
    num_str = match.group(1).replace(",", ".")
    try:
        return float(num_str)
    except ValueError:
        return None


def _is_time_field(field_name: str) -> bool:
    """Check if a field name corresponds to the time region."""
    return field_name == "time"


def _is_numeric_field(field_name: str) -> bool:
    """Check if a field name corresponds to a numeric value (speed/altitude)."""
    numeric_fields = {
        "speed_l",
        "speed_r",
        "altitude_l",
        "altitude_r",
    }
    return field_name in numeric_fields


class EasyOCREngine:
    """Wraps EasyOCR Reader with GPU/CPU auto-configuration."""

    def __init__(self, gpu_capabilities: GPUCapabilities) -> None:
        """Initialize EasyOCR Reader.

        EasyOCR uses PyTorch for inference. The gpu parameter is set based on
        CUDA availability detected at startup.

        Args:
            gpu_capabilities: Result of detect_gpu() call
        """
        self.reader = easyocr.Reader(
            lang_list=["en"],
            gpu=gpu_capabilities.gpu_available,
        )

    def extract_text(
        self,
        frame: np.ndarray,
        text_regions: dict[str, ROIRect],
        engine_bounding_boxes: list[ROIRect],
    ) -> OCRResult:
        """Extract text from non-occluded ROI regions.

        For each text region:
        1. Check if it intersects any engine bounding box → mark OCCLUDED_BY_ENGINES
        2. Crop frame to ROI rect
        3. Call self.reader.readtext(cropped_image) to get text predictions
        4. Parse result based on field type (float for speed/altitude, time format)
        5. If confidence is low or no text detected → mark UNAVAILABLE

        Args:
            frame: BGR numpy array (1920x1080)
            text_regions: Dict mapping field names to ROIRect objects
            engine_bounding_boxes: Bounding boxes of engine groups with detected engines

        Returns:
            OCRResult with extraction results for all text fields
        """
        field_results: dict[str, OCRFieldResult] = {}

        for field_name, roi in text_regions.items():
            field_results[field_name] = self._process_field(
                frame, field_name, roi, engine_bounding_boxes
            )

        # Build OCRResult, using UNAVAILABLE for any fields not in text_regions
        unavailable = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        return OCRResult(
            time=field_results.get("time", unavailable),
            speed_l=field_results.get("speed_l", unavailable),
            speed_l_unit=field_results.get("speed_l_unit", unavailable),
            speed_r=field_results.get("speed_r", unavailable),
            speed_r_unit=field_results.get("speed_r_unit", unavailable),
            altitude_l=field_results.get("altitude_l", unavailable),
            altitude_l_unit=field_results.get("altitude_l_unit", unavailable),
            altitude_r=field_results.get("altitude_r", unavailable),
            altitude_r_unit=field_results.get("altitude_r_unit", unavailable),
            stage_l=field_results.get("stage_l", unavailable),
            stage_r=field_results.get("stage_r", unavailable),
            stage_sep_text=field_results.get("stage_sep_text", unavailable),
        )

    def _process_field(
        self,
        frame: np.ndarray,
        field_name: str,
        roi: ROIRect,
        engine_bounding_boxes: list[ROIRect],
    ) -> OCRFieldResult:
        """Process a single text field: check occlusion, crop, OCR, and parse.

        Args:
            frame: Full BGR frame
            field_name: Name of the text field (e.g., "time", "speed_l")
            roi: ROI rectangle for this field
            engine_bounding_boxes: Active engine bounding boxes

        Returns:
            OCRFieldResult with appropriate status and parsed value
        """
        # Step 1: Check occlusion
        if _is_occluded_by_engines(roi, engine_bounding_boxes):
            return OCRFieldResult(status=OCRFieldStatus.OCCLUDED_BY_ENGINES)

        # Step 2: Crop frame to ROI
        cropped = _crop_frame_to_roi(frame, roi)

        if cropped.size == 0:
            return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        # Step 3: Run EasyOCR on cropped region
        results = self.reader.readtext(cropped)

        # Step 4: Extract best result
        if not results:
            return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        # Find the result with highest confidence
        best_result = max(results, key=lambda r: r[2])
        bbox, text, confidence = best_result

        # Step 5: Check confidence threshold
        if confidence < _CONFIDENCE_THRESHOLD or not text.strip():
            return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        raw_text = text.strip()

        # Step 6: Parse based on field type
        parsed_value = self._parse_field_value(field_name, raw_text)

        if parsed_value is None and (_is_time_field(field_name) or _is_numeric_field(field_name)):
            # Parsing failed for a field that requires specific format
            return OCRFieldResult(
                status=OCRFieldStatus.UNAVAILABLE,
                raw_text=raw_text,
            )

        return OCRFieldResult(
            status=OCRFieldStatus.AVAILABLE,
            raw_text=raw_text,
            parsed_value=parsed_value,
        )

    def _parse_field_value(
        self, field_name: str, raw_text: str
    ) -> float | str | None:
        """Parse the raw OCR text based on field type.

        Args:
            field_name: Name of the field to determine parsing strategy
            raw_text: Raw text extracted by EasyOCR

        Returns:
            Parsed value (float for numeric, str for time/text) or None
        """
        if _is_time_field(field_name):
            return _parse_time_value(raw_text)
        elif _is_numeric_field(field_name):
            return _parse_numeric_value(raw_text)
        else:
            # Unit fields, stage labels, and stage_sep_text are kept as strings
            return raw_text
