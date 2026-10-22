"""OCR Engine - extracts text values from frame regions using EasyOCR.

Uses EasyOCR with GPU acceleration (when available) to read text values
(time, speed, altitude, stage labels) from ROI regions.
"""

import os
import threading

import easyocr
import numpy as np
import torch

from src.core.enums import OCRFieldStatus
from src.core.gpu import GPUCapabilities
from src.core.models import ROIRect
from src.ocr.batcher import BatchedRecognizer
from src.ocr.cache import CACHEABLE_FIELDS, FieldCache, roi_signature
from src.ocr.parsing import (
    get_allowlist,
    is_numeric_field,
    is_time_field,
    is_unit_field,
    parse_field_value,
)
from src.ocr.regions import crop_frame_to_roi, roi_bounds, text_columns, to_gray
from src.ocr.results import OCRFieldResult, OCRResult


# Confidence threshold below which OCR results are marked UNAVAILABLE
_CONFIDENCE_THRESHOLD = 0.3


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
        # One recogniser pass serves every frame in flight (see ocr.batcher)
        self._batcher = BatchedRecognizer(self.reader)
        self._t_zero_detected: bool = False
        self._t_zero_lock = threading.Lock()
        # Results of the static ROIs, reused while their pixels are unchanged
        self._cache = FieldCache()

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

        gray = to_gray(frame)
        # One index per frame, so the cache refresh interval below counts
        # frames rather than recognition passes.
        frame_index = self._cache.next_frame_index()

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
                return OCRResult.unavailable()
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
                    return OCRResult.unavailable()
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
            return OCRResult.unavailable(time=time_result)
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
        cropped = crop_frame_to_roi(frame, roi)
        if cropped.size == 0:
            return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        results = self.reader.readtext(cropped, allowlist=get_allowlist(field_name))
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
        of every other frame being processed right now (see ``ocr.batcher``).

        Fields in ``CACHEABLE_FIELDS`` are kept out of the call entirely while
        their pixels are unchanged (see ``ocr.cache``), which is most of a
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
            x1, y1, x2, y2 = roi_bounds(gray, roi)
            if x2 <= x1 or y2 <= y1:
                results[field_name] = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
                continue
            # Fingerprint the fixed ROI, not the box narrowed to its text: the
            # narrowed edges move with the noise around the letters.
            signature = None
            if field_name in CACHEABLE_FIELDS:
                signature = roi_signature(gray, (x1, y1, x2, y2))
                cached = self._cache.lookup(field_name, signature, frame_index)
                if cached is not None:
                    results[field_name] = cached
                    continue
            if is_unit_field(field_name) or is_numeric_field(field_name):
                columns = text_columns(gray, x1, x2, y1, y2)
                if columns is None:
                    results[field_name] = OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)
                    continue
                x1, x2 = columns
            if signature is not None:
                signatures[field_name] = signature
            pending.append(field_name)
            items.append(([x1, x2, y1, y2], get_allowlist(field_name)))

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
                self._cache.store(
                    field_name, signatures[field_name], result, frame_index
                )

        return results

    def _build_field_result(
        self, field_name: str, text: str, confidence: float
    ) -> OCRFieldResult:
        """Apply the confidence threshold and parse a recognised string."""
        # Step 5: Check confidence threshold
        if confidence < _CONFIDENCE_THRESHOLD or not text.strip():
            return OCRFieldResult(status=OCRFieldStatus.UNAVAILABLE)

        raw_text = text.strip()

        # Step 6: Parse based on field type
        parsed_value = parse_field_value(field_name, raw_text)

        if parsed_value is None and (
            is_time_field(field_name)
            or is_numeric_field(field_name)
            or is_unit_field(field_name)
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

    def reset(self) -> None:
        """Reset T-0 detection and the static-ROI cache for a new pipeline run."""
        with self._t_zero_lock:
            self._t_zero_detected = False
        self._cache.clear()

    @property
    def t_zero_detected(self) -> bool:
        """Whether T-0 (00:00:00) has been detected."""
        with self._t_zero_lock:
            return self._t_zero_detected
