"""OCR Engine - extracts text values from frame regions using EasyOCR.

Uses EasyOCR with GPU acceleration (when available) to read text values
(time, speed, altitude, stage labels) from ROI regions.
"""

import re
import threading
from dataclasses import dataclass

import easyocr
import numpy as np

from src.enums import OCRFieldStatus
from src.gpu_detector import GPUCapabilities
from src.models import ROIRect


# Confidence threshold below which OCR results are marked UNAVAILABLE
_CONFIDENCE_THRESHOLD = 0.3

# Time format regex: HH:MM:SS (no sign prefix, just digits and colons)
_TIME_PATTERN = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")

# Numeric value regex: optional sign, digits with optional decimal point
_NUMERIC_PATTERN = re.compile(r"[+\-−]?\s*(\d+(?:[.,]\d+)?)")

# Allowed character sets for EasyOCR per field type
_ALLOWLIST_TIME = "0123456789:_"
_ALLOWLIST_NUMERIC = "0123456789."
_ALLOWLIST_UNIT = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ/"


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

    Expected format: HH:MM:SS (digits and colons only).
    Returns the raw time string as-is, or None if parsing fails.
    """
    match = _TIME_PATTERN.search(text)
    if not match:
        return None

    hours = match.group(1).zfill(2)
    minutes = match.group(2)
    seconds = match.group(3)

    # Validate ranges
    if int(minutes) > 59 or int(seconds) > 59:
        return None

    return f"{hours}:{minutes}:{seconds}"


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


def _is_unit_field(field_name: str) -> bool:
    """Check if a field name corresponds to a unit region."""
    unit_fields = {
        "speed_l_unit",
        "speed_r_unit",
        "altitude_l_unit",
        "altitude_r_unit",
    }
    return field_name in unit_fields


def _get_allowlist(field_name: str) -> str | None:
    """Return the EasyOCR allowlist for a given field, or None for no restriction."""
    if _is_time_field(field_name):
        return _ALLOWLIST_TIME
    elif _is_numeric_field(field_name):
        return _ALLOWLIST_NUMERIC
    elif _is_unit_field(field_name):
        return _ALLOWLIST_UNIT
    return None


class EasyOCREngine:
    """Wraps EasyOCR Reader with GPU/CPU auto-configuration.

    Tracks T-0 detection: OCR results are only emitted after the time ROI
    reads 00:00:00 for the first time, which marks liftoff (T-0).
    """

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
        self._t_zero_detected: bool = False
        self._t_zero_lock = threading.Lock()

    def extract_text(
        self,
        frame: np.ndarray,
        text_regions: dict[str, ROIRect],
    ) -> OCRResult:
        """Extract text from ROI regions.

        Processes the 'time' region first. If the time region yields no text,
        all other regions are skipped and marked UNAVAILABLE (early exit to
        avoid wasting OCR cycles on frames without telemetry overlay).

        For each remaining text region:
        1. Crop frame to ROI rect
        2. Call self.reader.readtext(cropped_image) to get text predictions
        3. Parse result based on field type (float for speed/altitude, time format)
        4. If confidence is low or no text detected → mark UNAVAILABLE

        Args:
            frame: BGR numpy array (1920x1080)
            text_regions: Dict mapping field names to ROIRect objects

        Returns:
            OCRResult with extraction results for all text fields
        """
        unavailable = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
        field_results: dict[str, OCRFieldResult] = {}

        # Process time ROI first as a gate check
        if "time" in text_regions:
            time_result = self._process_field(
                frame, "time", text_regions["time"]
            )

            # Read T-0 state under lock (first check of double-check pattern)
            with self._t_zero_lock:
                t_zero_was_detected = self._t_zero_detected

            # T-0 detection: wait for 00:00:00 before emitting telemetry
            if not t_zero_was_detected:
                if (
                    time_result.status == OCRFieldStatus.AVAILABLE
                    and time_result.parsed_value == "00:00:00"
                ):
                    # Double-check locking: acquire lock and verify state
                    with self._t_zero_lock:
                        if not self._t_zero_detected:
                            self._t_zero_detected = True
                    # Mark as T+00:00:00 for the output
                    time_result = OCRFieldResult(
                        status=OCRFieldStatus.AVAILABLE,
                        raw_text=time_result.raw_text,
                        parsed_value="T+00:00:00",
                    )
                else:
                    # T-0 not yet seen — skip all fields
                    return OCRResult(
                        time=unavailable,
                        speed_l=unavailable,
                        speed_l_unit=unavailable,
                        speed_r=unavailable,
                        speed_r_unit=unavailable,
                        altitude_l=unavailable,
                        altitude_l_unit=unavailable,
                        altitude_r=unavailable,
                        altitude_r_unit=unavailable,
                        stage_l=unavailable,
                        stage_r=unavailable,
                        stage_sep_text=unavailable,
                    )
            else:
                # T-0 already detected — prefix parsed time with T+
                if time_result.status == OCRFieldStatus.AVAILABLE and time_result.parsed_value:
                    time_result = OCRFieldResult(
                        status=OCRFieldStatus.AVAILABLE,
                        raw_text=time_result.raw_text,
                        parsed_value=f"T+{time_result.parsed_value}",
                    )

            field_results["time"] = time_result

            # Early exit: if time has no text, skip all other regions
            if time_result.status != OCRFieldStatus.AVAILABLE:
                return OCRResult(
                    time=time_result,
                    speed_l=unavailable,
                    speed_l_unit=unavailable,
                    speed_r=unavailable,
                    speed_r_unit=unavailable,
                    altitude_l=unavailable,
                    altitude_l_unit=unavailable,
                    altitude_r=unavailable,
                    altitude_r_unit=unavailable,
                    stage_l=unavailable,
                    stage_r=unavailable,
                    stage_sep_text=unavailable,
                )

        # Process remaining text regions
        for field_name, roi in text_regions.items():
            if field_name == "time":
                continue  # already processed
            field_results[field_name] = self._process_field(
                frame, field_name, roi
            )

        # Build OCRResult, using UNAVAILABLE for any fields not in text_regions
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
    ) -> OCRFieldResult:
        """Process a single text field: crop, OCR, and parse.

        Args:
            frame: Full BGR frame
            field_name: Name of the text field (e.g., "time", "speed_l")
            roi: ROI rectangle for this field

        Returns:
            OCRFieldResult with appropriate status and parsed value
        """
        # Step 1: Crop frame to ROI
        cropped = _crop_frame_to_roi(frame, roi)

        if cropped.size == 0:
            return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        # Step 3: Run EasyOCR on cropped region
        allowlist = _get_allowlist(field_name)
        results = self.reader.readtext(cropped, allowlist=allowlist)

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

    def reset(self) -> None:
        """Reset T-0 detection state for a new pipeline run."""
        with self._t_zero_lock:
            self._t_zero_detected = False

    @property
    def t_zero_detected(self) -> bool:
        """Whether T-0 (00:00:00) has been detected."""
        with self._t_zero_lock:
            return self._t_zero_detected
