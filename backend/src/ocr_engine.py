"""OCR Engine - extracts text values from frame regions using EasyOCR.

Uses EasyOCR with GPU acceleration (when available) to read text values
(time, speed, altitude, stage labels) from ROI regions.
"""

import os
import re
import threading
from dataclasses import dataclass

import cv2
import easyocr
import numpy as np
import torch

from src.enums import OCRFieldStatus
from src.gpu_detector import GPUCapabilities
from src.models import ROIRect
from src.ocr_batcher import BatchedRecognizer


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

# Recognised unit spellings, mapped to their canonical form. The slash in
# "KM/H" is often read as Z, I, L or 1, or dropped entirely.
_SPEED_UNIT_PATTERN = re.compile(r"KM[/ZIL1|]?H")
_SPEED_UNITS = {"MPH": "MPH"}
_ALTITUDE_UNITS = {"KM": "KM", "MI": "MI", "FT": "FT"}

# Unit and value ROIs are much wider than their text, and EasyOCR misreads
# short strings in a lot of padding: "KM" as garbage, and "2.7" km as "22.7"
# (the leading digit doubled). Their boxes are narrowed to the text first.
# Text pixels are those at least this bright (absolute floor, and relative to
# the brightest pixel in the ROI).
_INK_MIN_LEVEL = 120
_INK_RELATIVE_LEVEL = 0.6
_INK_PADDING = 3

# Fields whose text is static for long stretches of a flight: the unit labels
# and the stage names. Their ROIs are also the widest, and the recogniser's
# cost scales with ROI width rather than with how many ROIs are in the batch,
# so re-reading them on every frame is where most of the OCR time went. They
# are read once and then reused until their pixels change.
_CACHEABLE_FIELDS = frozenset({
    "speed_l_unit",
    "speed_r_unit",
    "altitude_l_unit",
    "altitude_r_unit",
    "stage_l",
    "stage_r",
    "stage_sep_text",
})

# The change detector reduces an ROI to the fraction of ink in each cell of a
# rows x columns grid, and compares two of those by their L1 difference
# relative to the ink they hold (see _signatures_match).
#
# An earlier version thresholded the grid to exact bits, and on compressed
# video the bits at the edges of the letters flipped from frame to frame: it
# missed 93% of unchanged ROIs on a busy H.264 scene, so the cache did nothing
# where it mattered most. An absolute difference fixed that but scaled with
# the amount of ink, so on thin, small text it missed real changes such as
# KM -> MI. Relative to the ink, measured:
#   unchanged ROIs, H.264 at 8 Mbit/s and 0.5 Mbit/s     <= 0.05
#   unchanged 1px-stroke text under sigma=25 noise       <= 0.26
#   any label change tried, thick or thin text           >= 0.67
_SIGNATURE_GRID = (8, 32)
_SIGNATURE_MAX_DIFF = 0.4

# Re-read a cached field at least this often (in frames) even when its
# signature has not changed, so a signature collision cannot persist.
_CACHE_REFRESH_FRAMES = 150


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

    @classmethod
    def unavailable(cls, time: OCRFieldResult | None = None) -> "OCRResult":
        """An OCRResult carrying no telemetry: every field UNAVAILABLE.

        Args:
            time: Optional time field to keep in place of UNAVAILABLE
                (e.g. the read that gated the rest of the frame out).
        """
        missing = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
        fields = {name: missing for name in cls.__dataclass_fields__}
        if time is not None:
            fields["time"] = time
        return cls(**fields)


def _roi_bounds(frame: np.ndarray, roi: ROIRect) -> tuple[int, int, int, int]:
    """Return (x1, y1, x2, y2) for the ROI, clamped to the frame boundaries."""
    h, w = frame.shape[:2]

    x1 = max(0, int(roi.x))
    y1 = max(0, int(roi.y))
    x2 = min(w, int(roi.x + roi.width))
    y2 = min(h, int(roi.y + roi.height))

    return x1, y1, x2, y2


def _crop_frame_to_roi(frame: np.ndarray, roi: ROIRect) -> np.ndarray:
    """Crop the frame to the given ROI rectangle.

    Clamps coordinates to frame boundaries to avoid out-of-bounds access.
    """
    x1, y1, x2, y2 = _roi_bounds(frame, roi)
    return frame[y1:y2, x1:x2]


def _to_gray(image: np.ndarray) -> np.ndarray:
    """Convert a BGR image to single-channel grayscale (no-op if already gray)."""
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


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


def _parse_unit_value(field_name: str, text: str) -> str | None:
    """Return the canonical unit for a unit field, or None if it is not one.

    Speed units must look like KM/H or MPH, altitude units like KM, MI or FT.
    Anything else (e.g. text OCR'd from the engine graphic that replaces a
    gauge before stage separation) is rejected.
    """
    normalized = re.sub(r"\s+", "", text).upper()
    if field_name.startswith("speed"):
        if _SPEED_UNIT_PATTERN.fullmatch(normalized):
            return "KM/H"
        return _SPEED_UNITS.get(normalized)
    return _ALTITUDE_UNITS.get(normalized)


def _text_columns(gray: np.ndarray, x1: int, x2: int, y1: int, y2: int) -> tuple[int, int] | None:
    """Narrow a unit or value ROI's horizontal extent to its bright text pixels.

    Returns the padded (x1, x2) span of the text, or None if the ROI holds
    no text-bright pixels at all.
    """
    crop = gray[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    level = max(_INK_MIN_LEVEL, int(crop.max()) * _INK_RELATIVE_LEVEL)
    columns = np.flatnonzero((crop > level).any(axis=0))
    if columns.size == 0:
        return None
    return (
        max(x1, x1 + int(columns[0]) - _INK_PADDING),
        min(x2, x1 + int(columns[-1]) + 1 + _INK_PADDING),
    )


def _roi_signature(
    gray: np.ndarray, box: tuple[int, int, int, int]
) -> np.ndarray | None:
    """Return a noise-tolerant fingerprint of what is written in an ROI.

    The crop is reduced to ink / no ink with the same level as
    ``_text_columns``, which drops the background showing through the overlay,
    and then averaged down to a fixed grid, which smooths out the letter edges
    that flicker between compressed frames. Compare two fingerprints with
    ``_signatures_match``. Returns None for an empty crop.
    """
    x1, y1, x2, y2 = box
    crop = gray[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    level = max(_INK_MIN_LEVEL, int(crop.max()) * _INK_RELATIVE_LEVEL)
    ink = (crop > level).astype(np.float32)
    rows, cols = _SIGNATURE_GRID
    return cv2.resize(ink, (cols, rows), interpolation=cv2.INTER_AREA)


def _signatures_match(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether two ROI fingerprints show the same text.

    The difference is taken relative to the ink in the fuller of the two, so
    it reads the same for thin and bold text: 0 for identical text, 1 for text
    against a blank ROI. Two blank ROIs match.
    """
    ink = max(float(a.sum()), float(b.sum()), 1.0)
    return float(np.abs(a - b).sum()) / ink < _SIGNATURE_MAX_DIFF


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


def configure_torch_threads() -> None:
    """Cap torch's intra-op threads at one, on CPU and GPU alike.

    Override with OCR_TORCH_THREADS.

    On CPU, the pipeline runs several OCR jobs at once, one per worker thread.
    If each of them also fans out over torch's own thread pool, the cores are
    oversubscribed and throughput collapses (measured roughly 10x lower than
    single-threaded workers on a 4-core CPU).

    On GPU the model runs on the device and the CPU side is only small tensor
    housekeeping, yet torch's OpenMP pool still spins after every op waiting
    for the next one, and with a recogniser pass every 10-20 ms it never goes
    to sleep. Measured on a 16-thread CPU at skip 30: its 7 workers burned 20%
    of the process's CPU doing nothing, CPU the video decoders needed; capped,
    throughput rose 52 -> 57 frames/s.
    """
    try:
        threads = int(os.environ.get("OCR_TORCH_THREADS", "1"))
    except ValueError:
        threads = 1
    torch.set_num_threads(max(1, threads))


class EasyOCREngine:
    """Wraps EasyOCR Reader with GPU/CPU auto-configuration.

    Tracks T-0 detection: OCR results are only emitted after the time ROI
    reads 00:00:00 for the first time, which marks liftoff (T-0).
    """

    def __init__(
        self,
        gpu_capabilities: GPUCapabilities,
        use_detection: bool | None = None,
    ) -> None:
        """Initialize EasyOCR Reader.

        EasyOCR uses PyTorch for inference. The gpu parameter is set based on
        CUDA availability detected at startup.

        Args:
            gpu_capabilities: Result of detect_gpu() call
            use_detection: Run EasyOCR's CRAFT text detector on every ROI
                (``readtext``) instead of recognising the fixed ROI box
                directly (``recognize``). Defaults to the ``OCR_USE_DETECTION``
                environment variable, off unless set to 1/true/yes.
        """
        configure_torch_threads()
        # quantize=False: EasyOCR's dynamic int8 quantisation (CPU only) was
        # slower than the float model in local measurements, not faster.
        self.reader = easyocr.Reader(
            lang_list=["en"],
            gpu=gpu_capabilities.gpu_available,
            quantize=False,
        )
        if use_detection is None:
            use_detection = os.environ.get("OCR_USE_DETECTION", "").lower() in {
                "1", "true", "yes",
            }
        self._use_detection = use_detection
        # One recogniser pass serves every frame in flight (see ocr_batcher)
        self._batcher = BatchedRecognizer(self.reader)
        self._t_zero_detected: bool = False
        self._t_zero_lock = threading.Lock()
        # field name -> (ROI signature, result, frame index it was read on).
        # Shared by the OCR worker threads, so it is guarded by its own lock.
        self._field_cache: dict[str, tuple[np.ndarray, OCRFieldResult, int]] = {}
        self._cache_lock = threading.Lock()
        self._frame_counter: int = 0

    def extract_text(
        self,
        frame: np.ndarray,
        text_regions: dict[str, ROIRect],
    ) -> OCRResult:
        """Extract text from ROI regions.

        Before T-0 only the time ROI is read, since nothing is emitted until it
        reads 00:00:00. After T-0 every ROI goes to the recogniser in a single
        batched pass: with the batcher, carrying the value ROIs along costs
        almost nothing, while a second pass per frame costs a whole recogniser
        invocation. A frame whose time ROI holds no text still reports nothing,
        it just finds that out after the pass rather than before it.

        Args:
            frame: BGR numpy array (1920x1080)
            text_regions: Dict mapping field names to ROIRect objects

        Returns:
            OCRResult with extraction results for all text fields
        """
        if self._use_detection:
            return self._extract_with_detection(frame, text_regions)

        gray = _to_gray(frame)
        # One index per frame, so the cache refresh interval below counts
        # frames rather than recognition passes.
        frame_index = self._next_frame_index()

        with self._t_zero_lock:
            t_zero_was_detected = self._t_zero_detected

        # Nothing to gate on when the caller did not ask for the time ROI
        if not t_zero_was_detected and "time" in text_regions:
            gate = self._recognize_fields(
                gray, {"time": text_regions["time"]}, frame_index
            )["time"]
            if not (
                gate.status == OCRFieldStatus.AVAILABLE
                and gate.parsed_value == "00:00:00"
            ):
                # T-0 not yet seen — nothing is emitted from this frame
                return self._all_unavailable()
            with self._t_zero_lock:
                self._t_zero_detected = True
            # This is the T-0 frame: keep the 00:00:00 just read and read the
            # rest, rather than reading the time ROI again in another batch
            others = {k: v for k, v in text_regions.items() if k != "time"}
            field_results = {**self._recognize_fields(gray, others, frame_index), "time": gate}
        else:
            field_results = self._recognize_fields(gray, text_regions, frame_index)
        return self._with_mission_time(field_results, text_regions)

    def _extract_with_detection(
        self,
        frame: np.ndarray,
        text_regions: dict[str, ROIRect],
    ) -> OCRResult:
        """Extract text by running CRAFT detection on each ROI in turn.

        The debug path (``OCR_USE_DETECTION``): every ROI is read on its own,
        so the time ROI still gates the rest to avoid wasting detections.
        """
        field_results: dict[str, OCRFieldResult] = {}
        if "time" in text_regions:
            with self._t_zero_lock:
                t_zero_was_detected = self._t_zero_detected
            gate = self._process_field(frame, "time", text_regions["time"])
            if not t_zero_was_detected:
                if not (
                    gate.status == OCRFieldStatus.AVAILABLE
                    and gate.parsed_value == "00:00:00"
                ):
                    return self._all_unavailable()
                with self._t_zero_lock:
                    self._t_zero_detected = True
            field_results["time"] = gate
            if gate.status != OCRFieldStatus.AVAILABLE:
                return self._with_mission_time(field_results, text_regions)

        for field_name, roi in text_regions.items():
            if field_name != "time":
                field_results[field_name] = self._process_field(frame, field_name, roi)
        return self._with_mission_time(field_results, text_regions)

    def _with_mission_time(
        self,
        field_results: dict[str, OCRFieldResult],
        text_regions: dict[str, ROIRect],
    ) -> OCRResult:
        """Prefix the read time with T+ and assemble the per-field result.

        A frame whose time ROI holds no text carries no telemetry, so every
        other field is reported UNAVAILABLE even when it was recognised. When
        the caller never asked for the time ROI there is nothing to gate on and
        the fields are reported as read.
        """
        unavailable = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
        if "time" not in text_regions:
            return self._build_result(field_results)
        time_result = field_results.get("time", unavailable)
        if time_result.status != OCRFieldStatus.AVAILABLE or not time_result.parsed_value:
            return self._all_unavailable(time=time_result)
        return self._build_result(
            {
                **field_results,
                "time": OCRFieldResult(
                    status=OCRFieldStatus.AVAILABLE,
                    raw_text=time_result.raw_text,
                    parsed_value=f"T+{time_result.parsed_value}",
                ),
            }
        )

    @staticmethod
    def _build_result(field_results: dict[str, OCRFieldResult]) -> OCRResult:
        """Assemble an OCRResult, defaulting any absent field to UNAVAILABLE."""
        unavailable = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
        return OCRResult(
            **{
                name: field_results.get(name, unavailable)
                for name in OCRResult.__dataclass_fields__
            }
        )

    @staticmethod
    def _all_unavailable(time: OCRFieldResult | None = None) -> OCRResult:
        """An OCRResult carrying no telemetry, optionally keeping the time read."""
        return OCRResult.unavailable(time=time)

    def _process_field(
        self,
        frame: np.ndarray,
        field_name: str,
        roi: ROIRect,
    ) -> OCRFieldResult:
        """Read one ROI with CRAFT detection, then parse it.

        Only the detection path uses this; the recognition path batches its
        ROIs instead (see ``_recognize_fields``).

        Args:
            frame: Full BGR frame
            field_name: Name of the text field (e.g., "time", "speed_l")
            roi: ROI rectangle for this field

        Returns:
            OCRFieldResult with appropriate status and parsed value
        """
        cropped = _crop_frame_to_roi(frame, roi)
        if cropped.size == 0:
            return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        results = self.reader.readtext(cropped, allowlist=_get_allowlist(field_name))
        if not results:
            return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        # Find the result with highest confidence
        best_result = max(results, key=lambda r: r[2])
        _, text, confidence = best_result
        return self._build_field_result(field_name, text, confidence)

    def _recognize_fields(
        self,
        gray: np.ndarray,
        regions: dict[str, ROIRect],
        frame_index: int,
    ) -> dict[str, OCRFieldResult]:
        """Recognise several fixed ROIs, batched with the other frames in flight.

        The ROIs are fixed boxes, so CRAFT detection is skipped and the
        recogniser runs on each box directly. All of them go to the batcher in
        one request, which runs them through the model together with the ROIs
        of every other frame being processed right now (see ``ocr_batcher``).

        Fields in ``_CACHEABLE_FIELDS`` are kept out of the call entirely while
        their pixels are unchanged (see ``_roi_signature``), which is most of a
        flight. Only the detection path (``_use_detection``) is left uncached.

        Args:
            gray: Full grayscale frame
            regions: Field names mapped to their ROI rectangles
            frame_index: This frame's index, for the cache refresh interval

        Returns:
            Field names mapped to their OCRFieldResult
        """
        if not regions:
            return {}

        pending: list[str] = []
        items: list[tuple[list[int], str | None]] = []
        results: dict[str, OCRFieldResult] = {}
        signatures: dict[str, np.ndarray] = {}
        for field_name, roi in regions.items():
            x1, y1, x2, y2 = _roi_bounds(gray, roi)
            if x2 <= x1 or y2 <= y1:
                results[field_name] = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
                continue
            # Fingerprint the fixed ROI, not the box narrowed to its text: the
            # narrowed edges move with the noise around the letters.
            signature = None
            if field_name in _CACHEABLE_FIELDS:
                signature = _roi_signature(gray, (x1, y1, x2, y2))
                cached = self._cached_result(field_name, signature, frame_index)
                if cached is not None:
                    results[field_name] = cached
                    continue
            if _is_unit_field(field_name) or _is_numeric_field(field_name):
                columns = _text_columns(gray, x1, x2, y1, y2)
                if columns is None:
                    results[field_name] = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
                    continue
                x1, x2 = columns
            if signature is not None:
                signatures[field_name] = signature
            pending.append(field_name)
            items.append(([x1, x2, y1, y2], _get_allowlist(field_name)))

        # Every ROI of this frame goes over in one request, so the frame waits
        # for a single batch however many allowlists it needs
        predictions = self._batcher.recognize(gray, items)
        # The batcher answers in the order the boxes were handed over
        for field_name, prediction in zip(pending, predictions):
            if prediction is None:
                result = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
            else:
                _, text, confidence = prediction
                result = self._build_field_result(field_name, text, confidence)
            results[field_name] = result
            if field_name in signatures:
                self._store_result(
                    field_name, signatures[field_name], result, frame_index
                )

        return results

    def _next_frame_index(self) -> int:
        """Allocate this frame's index for the cache refresh interval."""
        with self._cache_lock:
            self._frame_counter += 1
            return self._frame_counter

    def _cached_result(
        self, field_name: str, signature: np.ndarray | None, frame_index: int
    ) -> OCRFieldResult | None:
        """Return the stored result for an unchanged ROI, or None to read it.

        A cached field is re-read when its signature changes and at least every
        ``_CACHE_REFRESH_FRAMES`` frames regardless, so a stale entry cannot
        outlive a change the signature failed to register.
        """
        if signature is None:
            return None
        with self._cache_lock:
            entry = self._field_cache.get(field_name)
            if entry is not None:
                cached_signature, result, stored_at = entry
                if (
                    _signatures_match(cached_signature, signature)
                    and frame_index - stored_at < _CACHE_REFRESH_FRAMES
                ):
                    return result
            return None

    def _store_result(
        self,
        field_name: str,
        signature: np.ndarray,
        result: OCRFieldResult,
        frame_index: int,
    ) -> None:
        """Remember a freshly recognised result against its ROI signature."""
        with self._cache_lock:
            self._field_cache[field_name] = (signature, result, frame_index)

    def _build_field_result(
        self, field_name: str, text: str, confidence: float
    ) -> OCRFieldResult:
        """Apply the confidence threshold and parse a recognised string."""
        # Step 5: Check confidence threshold
        if confidence < _CONFIDENCE_THRESHOLD or not text.strip():
            return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        raw_text = text.strip()

        # Step 6: Parse based on field type
        parsed_value = self._parse_field_value(field_name, raw_text)

        if parsed_value is None and (
            _is_time_field(field_name)
            or _is_numeric_field(field_name)
            or _is_unit_field(field_name)
        ):
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
        elif _is_unit_field(field_name):
            return _parse_unit_value(field_name, raw_text)
        else:
            # Stage labels and stage_sep_text are kept as strings
            return raw_text

    def reset(self) -> None:
        """Reset T-0 detection and the static-ROI cache for a new pipeline run."""
        with self._t_zero_lock:
            self._t_zero_detected = False
        with self._cache_lock:
            self._field_cache.clear()
            self._frame_counter = 0

    @property
    def t_zero_detected(self) -> bool:
        """Whether T-0 (00:00:00) has been detected."""
        with self._t_zero_lock:
            return self._t_zero_detected
