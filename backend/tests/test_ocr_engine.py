"""Tests for the OCR Engine module.

Tests cover:
- Frame cropping to ROIs and narrowing value boxes to their text
- EasyOCREngine.extract_text with a mocked reader (per-field readtext path),
  including the T-0 gate

Parsing is covered in test_ocr_parsing.py and the default recognition-only
path in test_ocr_recognize.py.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.enums import OCRFieldStatus
from src.gpu_detector import AccelerationBackend, GPUCapabilities
from src.models import ROIRect
from src.ocr.engine import EasyOCREngine
from src.ocr.regions import crop_frame_to_roi, text_columns


# --- Fixtures ---


@pytest.fixture
def cpu_capabilities() -> GPUCapabilities:
    return GPUCapabilities(
        backend=AccelerationBackend.CPU,
        device_name=None,
        cuda_version=None,
        gpu_available=False,
    )


@pytest.fixture
def sample_frame() -> np.ndarray:
    """A 1920x1080 BGR frame filled with gray."""
    return np.full((1080, 1920, 3), 128, dtype=np.uint8)


@pytest.fixture
def text_regions() -> dict[str, ROIRect]:
    """Sample text regions for testing."""
    return {
        "time": ROIRect(id="time", x=800, y=10, width=320, height=50),
        "speed_l": ROIRect(id="speed_l", x=50, y=900, width=200, height=40),
        "speed_l_unit": ROIRect(id="speed_l_unit", x=50, y=940, width=100, height=30),
        "speed_r": ROIRect(id="speed_r", x=1700, y=900, width=200, height=40),
        "speed_r_unit": ROIRect(id="speed_r_unit", x=1700, y=940, width=100, height=30),
        "altitude_l": ROIRect(id="altitude_l", x=50, y=800, width=200, height=40),
        "altitude_l_unit": ROIRect(id="altitude_l_unit", x=50, y=840, width=100, height=30),
        "altitude_r": ROIRect(id="altitude_r", x=1700, y=800, width=200, height=40),
        "altitude_r_unit": ROIRect(id="altitude_r_unit", x=1700, y=840, width=100, height=30),
        "stage_l": ROIRect(id="stage_l", x=100, y=700, width=200, height=40),
        "stage_r": ROIRect(id="stage_r", x=1620, y=700, width=200, height=40),
        "stage_sep_text": ROIRect(id="stage_sep_text", x=860, y=500, width=200, height=40),
    }


_BOX = [[0, 0], [100, 0], [100, 30], [0, 30]]


def make_engine(mock_reader_cls, capabilities, readtext) -> EasyOCREngine:
    """Build an engine on the per-field readtext path with a mocked reader.

    Args:
        readtext: list of (text, confidence) returned for successive calls;
            the time ROI is always read first. The last entry repeats.
    """
    mock_reader = MagicMock()
    mock_reader_cls.return_value = mock_reader
    calls = {"n": 0}

    def readtext_side_effect(img, allowlist=None):
        text, confidence = readtext[min(calls["n"], len(readtext) - 1)]
        calls["n"] += 1
        return [(_BOX, text, confidence)] if text is not None else []

    mock_reader.readtext.side_effect = readtext_side_effect
    return EasyOCREngine(capabilities, use_detection=True)


# --- Text Column Tests ---


class TestTextColumns:
    def test_narrows_to_bright_text(self):
        gray = np.zeros((15, 44), dtype=np.uint8)
        gray[3:12, 14:30] = 220
        assert text_columns(gray, 0, 44, 0, 15) == (11, 33)

    def test_respects_roi_offset_and_bounds(self):
        gray = np.zeros((20, 100), dtype=np.uint8)
        gray[5:15, 51:60] = 220
        assert text_columns(gray, 50, 70, 5, 15) == (50, 63)

    def test_no_text_returns_none(self):
        gray = np.full((15, 44), 40, dtype=np.uint8)
        assert text_columns(gray, 0, 44, 0, 15) is None


# --- Frame Cropping Tests ---


class TestCropFrameToROI:
    def test_basic_crop(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        roi = ROIRect(id="test", x=10, y=20, width=50, height=30)
        cropped = crop_frame_to_roi(frame, roi)
        assert cropped.shape == (30, 50, 3)

    def test_clamped_to_boundaries(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        roi = ROIRect(id="test", x=180, y=80, width=50, height=50)
        cropped = crop_frame_to_roi(frame, roi)
        # Should be clamped: x from 180 to 200 (w=20), y from 80 to 100 (h=20)
        assert cropped.shape == (20, 20, 3)

    def test_zero_size_roi(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        roi = ROIRect(id="test", x=0, y=0, width=0, height=0)
        cropped = crop_frame_to_roi(frame, roi)
        assert cropped.size == 0


# --- EasyOCREngine Integration Tests (mocked reader) ---


class TestEasyOCREngineExtractText:
    @patch("src.ocr.engine.easyocr.Reader")
    def test_t_zero_marks_liftoff(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Reading 00:00:00 detects T-0 and emits T+00:00:00."""
        engine = make_engine(mock_reader_cls, cpu_capabilities, [("00:00:00", 0.92)])
        result = engine.extract_text(sample_frame, {"time": text_regions["time"]})

        assert engine.t_zero_detected
        assert result.time.status == OCRFieldStatus.AVAILABLE
        assert result.time.raw_text == "00:00:00"
        assert result.time.parsed_value == "T+00:00:00"

    @patch("src.ocr.engine.easyocr.Reader")
    def test_fields_gated_until_t_zero(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Before T-0 every field is UNAVAILABLE, even a readable time."""
        engine = make_engine(
            mock_reader_cls, cpu_capabilities, [("00:01:30", 0.95), ("1523.5", 0.9)]
        )
        result = engine.extract_text(sample_frame, text_regions)

        assert not engine.t_zero_detected
        assert result.time.status == OCRFieldStatus.UNAVAILABLE
        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE
        # Only the time ROI was read
        assert mock_reader_cls.return_value.readtext.call_count == 1

    @patch("src.ocr.engine.easyocr.Reader")
    def test_time_prefixed_after_t_zero(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Once T-0 has been seen, later times are emitted with a T+ prefix."""
        engine = make_engine(
            mock_reader_cls, cpu_capabilities, [("00:00:00", 0.95), ("00:02:35", 0.95)]
        )
        time_only = {"time": text_regions["time"]}
        engine.extract_text(sample_frame, time_only)
        result = engine.extract_text(sample_frame, time_only)

        assert result.time.parsed_value == "T+00:02:35"

    @patch("src.ocr.engine.easyocr.Reader")
    def test_successful_numeric_extraction(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Speed/altitude fields should be parsed as float."""
        engine = make_engine(
            mock_reader_cls, cpu_capabilities, [("00:00:00", 0.95), ("1523.5", 0.88)]
        )
        result = engine.extract_text(sample_frame, text_regions)

        assert result.speed_l.status == OCRFieldStatus.AVAILABLE
        assert result.speed_l.parsed_value == 1523.5

    @patch("src.ocr.engine.easyocr.Reader")
    def test_low_confidence_marked_unavailable(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Low confidence results (< 0.3) should be UNAVAILABLE."""
        engine = make_engine(
            mock_reader_cls, cpu_capabilities, [("00:00:00", 0.95), ("123", 0.2)]
        )
        result = engine.extract_text(sample_frame, text_regions)

        assert result.time.status == OCRFieldStatus.AVAILABLE
        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE

    @patch("src.ocr.engine.easyocr.Reader")
    def test_low_confidence_time_skips_other_fields(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """An unreadable time ROI exits early without reading other ROIs."""
        engine = make_engine(mock_reader_cls, cpu_capabilities, [("00:00:00", 0.2)])
        result = engine.extract_text(sample_frame, text_regions)

        assert result.time.status == OCRFieldStatus.UNAVAILABLE
        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE
        assert mock_reader_cls.return_value.readtext.call_count == 1

    @patch("src.ocr.engine.easyocr.Reader")
    def test_empty_readtext_result(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Empty OCR result should be marked UNAVAILABLE."""
        engine = make_engine(
            mock_reader_cls, cpu_capabilities, [("00:00:00", 0.95), (None, 0.0)]
        )
        result = engine.extract_text(sample_frame, text_regions)

        assert result.time.status == OCRFieldStatus.AVAILABLE
        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE

    @patch("src.ocr.engine.easyocr.Reader")
    def test_unit_field_kept_as_string(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Unit fields should keep raw text as parsed value."""
        engine = make_engine(
            mock_reader_cls, cpu_capabilities, [("00:00:00", 0.95), ("KM/H", 0.90)]
        )
        result = engine.extract_text(sample_frame, text_regions)

        assert result.speed_l_unit.status == OCRFieldStatus.AVAILABLE
        assert result.speed_l_unit.parsed_value == "KM/H"

    @patch("src.ocr.engine.easyocr.Reader")
    def test_stage_label_extraction(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Stage labels should be extracted as strings."""
        engine = make_engine(
            mock_reader_cls, cpu_capabilities, [("00:00:00", 0.95), ("SUPER HEAVY", 0.85)]
        )
        result = engine.extract_text(sample_frame, text_regions)

        assert result.stage_l.status == OCRFieldStatus.AVAILABLE
        assert result.stage_l.parsed_value == "SUPER HEAVY"

    @patch("src.ocr.engine.easyocr.Reader")
    def test_stage_sep_text_extraction(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Stage separation text should be extracted as string."""
        engine = make_engine(
            mock_reader_cls, cpu_capabilities, [("00:00:00", 0.95), ("STAGE SEP", 0.80)]
        )
        result = engine.extract_text(sample_frame, text_regions)

        assert result.stage_sep_text.status == OCRFieldStatus.AVAILABLE
        assert result.stage_sep_text.parsed_value == "STAGE SEP"

    @patch("src.ocr.engine.easyocr.Reader")
    def test_missing_region_defaults_to_unavailable(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Fields not present in text_regions should default to UNAVAILABLE."""
        engine = make_engine(mock_reader_cls, cpu_capabilities, [("00:00:00", 0.95)])
        result = engine.extract_text(sample_frame, {"time": text_regions["time"]})

        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE
        assert result.altitude_r.status == OCRFieldStatus.UNAVAILABLE

    @patch("src.ocr.engine.easyocr.Reader")
    def test_numeric_parse_failure_marks_unavailable(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """If numeric field gets non-numeric text, mark UNAVAILABLE."""
        engine = make_engine(mock_reader_cls, cpu_capabilities, [("HELLO", 0.90)])

        # Only test speed_l (no time ROI, so no T-0 gate) to isolate the behavior
        result = engine.extract_text(sample_frame, {"speed_l": text_regions["speed_l"]})

        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE
        assert result.speed_l.raw_text == "HELLO"

    @patch("src.ocr.engine.easyocr.Reader")
    def test_gpu_flag_passed_to_reader(self, mock_reader_cls):
        """GPU capabilities should be forwarded to EasyOCR Reader."""
        gpu_caps = GPUCapabilities(
            backend=AccelerationBackend.CUDA_GPU,
            device_name="NVIDIA RTX 4090",
            cuda_version="12.2",
            gpu_available=True,
        )

        EasyOCREngine(gpu_caps)

        mock_reader_cls.assert_called_once_with(
            lang_list=["en"],
            gpu=True,
            quantize=False,
        )


# --- OCRResult.unavailable ---


class TestOCRResultUnavailable:
    """OCRResult.unavailable() builds a result carrying no telemetry."""

    @staticmethod
    def _field_names() -> list[str]:
        from src.ocr.results import OCRResult

        return list(OCRResult.__dataclass_fields__)

    def test_every_field_unavailable(self):
        from src.ocr.results import OCRResult

        result = OCRResult.unavailable()
        for name in self._field_names():
            field = getattr(result, name)
            assert field.status == OCRFieldStatus.UNAVAILABLE, name
            assert field.raw_text is None, name
            assert field.parsed_value is None, name

    def test_time_override_kept(self):
        from src.ocr.results import OCRFieldResult, OCRResult

        time_read = OCRFieldResult(
            status=OCRFieldStatus.AVAILABLE, raw_text="T-00:01:00", parsed_value="-00:01:00"
        )
        result = OCRResult.unavailable(time=time_read)
        assert result.time is time_read
        for name in self._field_names():
            if name != "time":
                assert getattr(result, name).status == OCRFieldStatus.UNAVAILABLE, name

    def test_time_none_means_unavailable(self):
        from src.ocr.results import OCRResult

        assert OCRResult.unavailable(time=None) == OCRResult.unavailable()

    def test_engine_all_unavailable_delegates(self):
        from src.ocr.results import OCRFieldResult, OCRResult

        time_read = OCRFieldResult(status=OCRFieldStatus.AVAILABLE, raw_text="T-00:00:05")
        assert EasyOCREngine._all_unavailable() == OCRResult.unavailable()
        assert EasyOCREngine._all_unavailable(time_read) == OCRResult.unavailable(time=time_read)
