"""Tests for the OCR Engine module.

Tests cover:
- ROI intersection logic (occlusion detection)
- Time and numeric value parsing
- OCRFieldResult/OCRResult construction
- EasyOCREngine.extract_text with mocked reader
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.enums import OCRFieldStatus
from src.gpu_detector import AccelerationBackend, GPUCapabilities
from src.models import ROIRect
from src.ocr_engine import (
    EasyOCREngine,
    OCRFieldResult,
    OCRResult,
    _crop_frame_to_roi,
    _is_occluded_by_engines,
    _parse_numeric_value,
    _parse_time_value,
    _rects_intersect,
)


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


# --- Rectangle Intersection Tests ---


class TestRectsIntersect:
    def test_no_overlap_horizontal(self):
        a = ROIRect(id="a", x=0, y=0, width=50, height=50)
        b = ROIRect(id="b", x=60, y=0, width=50, height=50)
        assert _rects_intersect(a, b) is False

    def test_no_overlap_vertical(self):
        a = ROIRect(id="a", x=0, y=0, width=50, height=50)
        b = ROIRect(id="b", x=0, y=60, width=50, height=50)
        assert _rects_intersect(a, b) is False

    def test_overlap(self):
        a = ROIRect(id="a", x=0, y=0, width=50, height=50)
        b = ROIRect(id="b", x=25, y=25, width=50, height=50)
        assert _rects_intersect(a, b) is True

    def test_touching_edges_no_overlap(self):
        """Touching edges (no shared area) should NOT count as intersection."""
        a = ROIRect(id="a", x=0, y=0, width=50, height=50)
        b = ROIRect(id="b", x=50, y=0, width=50, height=50)
        assert _rects_intersect(a, b) is False

    def test_contained(self):
        """One rect fully contained in another should intersect."""
        outer = ROIRect(id="outer", x=0, y=0, width=100, height=100)
        inner = ROIRect(id="inner", x=25, y=25, width=50, height=50)
        assert _rects_intersect(outer, inner) is True
        assert _rects_intersect(inner, outer) is True

    def test_same_rect(self):
        a = ROIRect(id="a", x=10, y=10, width=50, height=50)
        assert _rects_intersect(a, a) is True


class TestIsOccludedByEngines:
    def test_not_occluded_empty_list(self):
        roi = ROIRect(id="time", x=800, y=10, width=320, height=50)
        assert _is_occluded_by_engines(roi, []) is False

    def test_occluded_by_one_bbox(self):
        roi = ROIRect(id="speed_l", x=50, y=900, width=200, height=40)
        engine_bbox = ROIRect(id="engines", x=40, y=890, width=250, height=100)
        assert _is_occluded_by_engines(roi, [engine_bbox]) is True

    def test_not_occluded_separate_bbox(self):
        roi = ROIRect(id="time", x=800, y=10, width=320, height=50)
        engine_bbox = ROIRect(id="engines", x=0, y=800, width=200, height=200)
        assert _is_occluded_by_engines(roi, [engine_bbox]) is False


# --- Parsing Tests ---


class TestParseTimeValue:
    def test_standard_positive(self):
        assert _parse_time_value("T+01:23:45") == "T+01:23:45"

    def test_standard_negative(self):
        assert _parse_time_value("T-00:05:30") == "T-00:05:30"

    def test_unicode_minus(self):
        assert _parse_time_value("T−02:10:00") == "T-02:10:00"

    def test_no_sign(self):
        # If no sign after T, default to +
        assert _parse_time_value("T 00:01:15") == "T+00:01:15"

    def test_invalid_format(self):
        assert _parse_time_value("12:34:56") is None

    def test_with_noise(self):
        assert _parse_time_value("  T+00:02:35  ") == "T+00:02:35"

    def test_single_digit_hour(self):
        assert _parse_time_value("T+1:02:03") == "T+01:02:03"


class TestParseNumericValue:
    def test_integer(self):
        assert _parse_numeric_value("1523") == 1523.0

    def test_decimal(self):
        assert _parse_numeric_value("48.2") == 48.2

    def test_with_comma_decimal(self):
        assert _parse_numeric_value("1,5") == 1.5

    def test_with_surrounding_noise(self):
        assert _parse_numeric_value("  2100.5 km/h") == 2100.5

    def test_no_number(self):
        assert _parse_numeric_value("KM/H") is None

    def test_empty_string(self):
        assert _parse_numeric_value("") is None


# --- Frame Cropping Tests ---


class TestCropFrameToROI:
    def test_basic_crop(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        roi = ROIRect(id="test", x=10, y=20, width=50, height=30)
        cropped = _crop_frame_to_roi(frame, roi)
        assert cropped.shape == (30, 50, 3)

    def test_clamped_to_boundaries(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        roi = ROIRect(id="test", x=180, y=80, width=50, height=50)
        cropped = _crop_frame_to_roi(frame, roi)
        # Should be clamped: x from 180 to 200 (w=20), y from 80 to 100 (h=20)
        assert cropped.shape == (20, 20, 3)

    def test_zero_size_roi(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        roi = ROIRect(id="test", x=0, y=0, width=0, height=0)
        cropped = _crop_frame_to_roi(frame, roi)
        assert cropped.size == 0


# --- EasyOCREngine Integration Tests (mocked reader) ---


class TestEasyOCREngineExtractText:
    @patch("src.ocr_engine.easyocr.Reader")
    def test_occluded_field_marked_correctly(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Fields overlapping engine bboxes should be OCCLUDED_BY_ENGINES."""
        mock_reader_cls.return_value = MagicMock()
        mock_reader_cls.return_value.readtext.return_value = [
            ([[0, 0], [100, 0], [100, 30], [0, 30]], "T+00:01:00", 0.95)
        ]

        engine = EasyOCREngine(cpu_capabilities)

        # Engine bbox overlaps speed_l region
        engine_bboxes = [ROIRect(id="eng", x=40, y=890, width=220, height=100)]

        result = engine.extract_text(sample_frame, text_regions, engine_bboxes)

        assert result.speed_l.status == OCRFieldStatus.OCCLUDED_BY_ENGINES
        assert result.speed_l_unit.status == OCRFieldStatus.OCCLUDED_BY_ENGINES

    @patch("src.ocr_engine.easyocr.Reader")
    def test_successful_time_extraction(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Time field should be parsed when OCR returns valid time string."""
        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader
        mock_reader.readtext.return_value = [
            ([[0, 0], [100, 0], [100, 30], [0, 30]], "T+00:02:35", 0.92)
        ]

        engine = EasyOCREngine(cpu_capabilities)
        result = engine.extract_text(sample_frame, text_regions, [])

        assert result.time.status == OCRFieldStatus.AVAILABLE
        assert result.time.raw_text == "T+00:02:35"
        assert result.time.parsed_value == "T+00:02:35"

    @patch("src.ocr_engine.easyocr.Reader")
    def test_successful_numeric_extraction(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Speed/altitude fields should be parsed as float."""
        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader

        # First call is for the time ROI (must succeed to pass early-exit gate),
        # subsequent calls return a numeric value.
        def readtext_side_effect(img):
            # Return a valid time for the time ROI (detected by call order: first call)
            if readtext_side_effect.call_count == 0:
                readtext_side_effect.call_count += 1
                return [([[0, 0], [100, 0], [100, 30], [0, 30]], "T+00:01:30", 0.95)]
            readtext_side_effect.call_count += 1
            return [([[0, 0], [100, 0], [100, 30], [0, 30]], "1523.5", 0.88)]

        readtext_side_effect.call_count = 0
        mock_reader.readtext.side_effect = readtext_side_effect

        engine = EasyOCREngine(cpu_capabilities)
        result = engine.extract_text(sample_frame, text_regions, [])

        assert result.speed_l.status == OCRFieldStatus.AVAILABLE
        assert result.speed_l.parsed_value == 1523.5

    @patch("src.ocr_engine.easyocr.Reader")
    def test_low_confidence_marked_unavailable(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Low confidence results (< 0.3) should be UNAVAILABLE."""
        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader
        mock_reader.readtext.return_value = [
            ([[0, 0], [100, 0], [100, 30], [0, 30]], "123", 0.2)
        ]

        engine = EasyOCREngine(cpu_capabilities)
        result = engine.extract_text(sample_frame, text_regions, [])

        assert result.time.status == OCRFieldStatus.UNAVAILABLE
        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE

    @patch("src.ocr_engine.easyocr.Reader")
    def test_empty_readtext_result(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Empty OCR result should be marked UNAVAILABLE."""
        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader
        mock_reader.readtext.return_value = []

        engine = EasyOCREngine(cpu_capabilities)
        result = engine.extract_text(sample_frame, text_regions, [])

        assert result.time.status == OCRFieldStatus.UNAVAILABLE
        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE

    @patch("src.ocr_engine.easyocr.Reader")
    def test_unit_field_kept_as_string(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Unit fields should keep raw text as parsed value."""
        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader

        def readtext_side_effect(img):
            if readtext_side_effect.call_count == 0:
                readtext_side_effect.call_count += 1
                return [([[0, 0], [100, 0], [100, 30], [0, 30]], "T+00:01:30", 0.95)]
            readtext_side_effect.call_count += 1
            return [([[0, 0], [50, 0], [50, 20], [0, 20]], "KM/H", 0.90)]

        readtext_side_effect.call_count = 0
        mock_reader.readtext.side_effect = readtext_side_effect

        engine = EasyOCREngine(cpu_capabilities)
        result = engine.extract_text(sample_frame, text_regions, [])

        assert result.speed_l_unit.status == OCRFieldStatus.AVAILABLE
        assert result.speed_l_unit.parsed_value == "KM/H"

    @patch("src.ocr_engine.easyocr.Reader")
    def test_stage_label_extraction(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Stage labels should be extracted as strings."""
        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader

        def readtext_side_effect(img):
            if readtext_side_effect.call_count == 0:
                readtext_side_effect.call_count += 1
                return [([[0, 0], [100, 0], [100, 30], [0, 30]], "T+00:01:30", 0.95)]
            readtext_side_effect.call_count += 1
            return [([[0, 0], [100, 0], [100, 30], [0, 30]], "SUPER HEAVY", 0.85)]

        readtext_side_effect.call_count = 0
        mock_reader.readtext.side_effect = readtext_side_effect

        engine = EasyOCREngine(cpu_capabilities)
        result = engine.extract_text(sample_frame, text_regions, [])

        assert result.stage_l.status == OCRFieldStatus.AVAILABLE
        assert result.stage_l.parsed_value == "SUPER HEAVY"

    @patch("src.ocr_engine.easyocr.Reader")
    def test_stage_sep_text_extraction(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """Stage separation text should be extracted as string."""
        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader

        def readtext_side_effect(img):
            if readtext_side_effect.call_count == 0:
                readtext_side_effect.call_count += 1
                return [([[0, 0], [100, 0], [100, 30], [0, 30]], "T+00:01:30", 0.95)]
            readtext_side_effect.call_count += 1
            return [([[0, 0], [100, 0], [100, 30], [0, 30]], "STAGE SEP", 0.80)]

        readtext_side_effect.call_count = 0
        mock_reader.readtext.side_effect = readtext_side_effect

        engine = EasyOCREngine(cpu_capabilities)
        result = engine.extract_text(sample_frame, text_regions, [])

        assert result.stage_sep_text.status == OCRFieldStatus.AVAILABLE
        assert result.stage_sep_text.parsed_value == "STAGE SEP"

    @patch("src.ocr_engine.easyocr.Reader")
    def test_missing_region_defaults_to_unavailable(
        self, mock_reader_cls, cpu_capabilities, sample_frame
    ):
        """Fields not present in text_regions should default to UNAVAILABLE."""
        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader
        mock_reader.readtext.return_value = [
            ([[0, 0], [100, 0], [100, 30], [0, 30]], "T+00:01:00", 0.95)
        ]

        engine = EasyOCREngine(cpu_capabilities)
        # Only provide the time region
        result = engine.extract_text(
            sample_frame,
            {"time": ROIRect(id="time", x=800, y=10, width=320, height=50)},
            [],
        )

        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE
        assert result.altitude_r.status == OCRFieldStatus.UNAVAILABLE

    @patch("src.ocr_engine.easyocr.Reader")
    def test_numeric_parse_failure_marks_unavailable(
        self, mock_reader_cls, cpu_capabilities, sample_frame, text_regions
    ):
        """If numeric field gets non-numeric text, mark UNAVAILABLE."""
        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader
        mock_reader.readtext.return_value = [
            ([[0, 0], [100, 0], [100, 30], [0, 30]], "HELLO", 0.90)
        ]

        engine = EasyOCREngine(cpu_capabilities)

        # Only test speed_l to isolate the behavior
        result = engine.extract_text(
            sample_frame,
            {"speed_l": text_regions["speed_l"]},
            [],
        )

        assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE
        assert result.speed_l.raw_text == "HELLO"

    @patch("src.ocr_engine.easyocr.Reader")
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
        )


# ─── Property-Based Tests ─────────────────────────────────────────────────────

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from hypothesis.strategies import composite


# ─── Strategies ───────────────────────────────────────────────────────────────


@composite
def roi_rect_strategy(draw, min_x=0.0, max_x=1800.0, min_y=0.0, max_y=980.0):
    """Strategy for generating valid ROIRect instances within a 1920x1080 frame."""
    x = draw(st.floats(min_value=min_x, max_value=max_x, allow_nan=False, allow_infinity=False))
    y = draw(st.floats(min_value=min_y, max_value=max_y, allow_nan=False, allow_infinity=False))
    # Ensure width/height are positive and rect stays within frame
    max_w = min(1920.0 - x, 500.0)
    max_h = min(1080.0 - y, 500.0)
    width = draw(st.floats(min_value=1.0, max_value=max(1.0, max_w), allow_nan=False, allow_infinity=False))
    height = draw(st.floats(min_value=1.0, max_value=max(1.0, max_h), allow_nan=False, allow_infinity=False))
    rect_id = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_"),
        min_size=2,
        max_size=8,
    ))
    return ROIRect(id=rect_id, x=x, y=y, width=width, height=height)


@composite
def non_intersecting_roi_pair(draw):
    """Strategy for generating two ROIRects that definitely do NOT intersect."""
    # Place rect A in the left half, rect B in the right half with a gap
    a_x = draw(st.floats(min_value=0.0, max_value=400.0, allow_nan=False, allow_infinity=False))
    a_y = draw(st.floats(min_value=0.0, max_value=400.0, allow_nan=False, allow_infinity=False))
    a_w = draw(st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False))
    a_h = draw(st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False))

    # Ensure b starts after a ends with at least 1 pixel gap
    b_x = draw(st.floats(
        min_value=a_x + a_w + 1.0,
        max_value=a_x + a_w + 500.0,
        allow_nan=False,
        allow_infinity=False,
    ))
    b_y = draw(st.floats(
        min_value=a_y + a_h + 1.0,
        max_value=a_y + a_h + 500.0,
        allow_nan=False,
        allow_infinity=False,
    ))
    b_w = draw(st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False))
    b_h = draw(st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False))

    a = ROIRect(id="a", x=a_x, y=a_y, width=a_w, height=a_h)
    b = ROIRect(id="b", x=b_x, y=b_y, width=b_w, height=b_h)
    return a, b


@composite
def intersecting_roi_pair(draw):
    """Strategy for generating two ROIRects that definitely DO intersect (overlap)."""
    # Create rect A
    a_x = draw(st.floats(min_value=0.0, max_value=800.0, allow_nan=False, allow_infinity=False))
    a_y = draw(st.floats(min_value=0.0, max_value=800.0, allow_nan=False, allow_infinity=False))
    a_w = draw(st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False))
    a_h = draw(st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False))

    # Create rect B that overlaps with A by placing B's origin inside A
    b_x = draw(st.floats(
        min_value=a_x + 0.1,
        max_value=a_x + a_w - 0.1,
        allow_nan=False,
        allow_infinity=False,
    ))
    b_y = draw(st.floats(
        min_value=a_y + 0.1,
        max_value=a_y + a_h - 0.1,
        allow_nan=False,
        allow_infinity=False,
    ))
    b_w = draw(st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False))
    b_h = draw(st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False))

    a = ROIRect(id="a", x=a_x, y=a_y, width=a_w, height=a_h)
    b = ROIRect(id="b", x=b_x, y=b_y, width=b_w, height=b_h)
    return a, b


@composite
def valid_integer_string(draw):
    """Strategy for generating valid integer strings."""
    value = draw(st.integers(min_value=0, max_value=99999))
    return str(value), float(value)


@composite
def valid_decimal_string(draw):
    """Strategy for generating valid decimal strings."""
    integer_part = draw(st.integers(min_value=0, max_value=9999))
    decimal_part = draw(st.integers(min_value=0, max_value=99))
    decimal_str = f"{integer_part}.{decimal_part}"
    expected_value = float(decimal_str)
    return decimal_str, expected_value


@composite
def valid_time_string(draw):
    """Strategy for generating valid T+HH:MM:SS / T-HH:MM:SS strings."""
    sign = draw(st.sampled_from(["+", "-"]))
    hours = draw(st.integers(min_value=0, max_value=99))
    minutes = draw(st.integers(min_value=0, max_value=59))
    seconds = draw(st.integers(min_value=0, max_value=59))
    time_str = f"T{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    return time_str


# ─── Property 7: OCR Occlusion Logic Consistency ─────────────────────────────


class TestPropertyOCROcclusion:
    """Property 7: OCR Occlusion Logic Consistency.

    For any text ROI rect and list of engine bounding boxes, if the text ROI
    geometrically intersects any engine bounding box that has detected engines,
    then that text field SHALL be marked "occluded_by_engines"; otherwise it
    SHALL be processed for OCR.

    **Validates: Requirements 4.1, 4.7**
    """

    @given(data=intersecting_roi_pair())
    @settings(max_examples=100)
    def test_intersecting_rects_always_detected(self, data):
        """_rects_intersect returns True for any pair of overlapping rects."""
        a, b = data
        assert _rects_intersect(a, b) is True

    @given(data=non_intersecting_roi_pair())
    @settings(max_examples=100)
    def test_non_intersecting_rects_never_detected(self, data):
        """_rects_intersect returns False for any pair of non-overlapping rects."""
        a, b = data
        assert _rects_intersect(a, b) is False

    @given(rect=roi_rect_strategy())
    @settings(max_examples=100)
    def test_rect_always_intersects_itself(self, rect):
        """Any rect must intersect with itself (reflexivity)."""
        assert _rects_intersect(rect, rect) is True

    @given(data=intersecting_roi_pair())
    @settings(max_examples=100)
    def test_intersection_is_symmetric(self, data):
        """If A intersects B, then B intersects A (commutativity)."""
        a, b = data
        assert _rects_intersect(a, b) == _rects_intersect(b, a)

    @given(text_roi=roi_rect_strategy(), engine_bbox=roi_rect_strategy())
    @settings(max_examples=100)
    def test_occlusion_consistent_with_intersection(self, text_roi, engine_bbox):
        """_is_occluded_by_engines with a single bbox equals _rects_intersect result."""
        expected = _rects_intersect(text_roi, engine_bbox)
        actual = _is_occluded_by_engines(text_roi, [engine_bbox])
        assert actual == expected

    @given(text_roi=roi_rect_strategy())
    @settings(max_examples=100)
    def test_empty_engine_list_never_occludes(self, text_roi):
        """No engine bounding boxes means no occlusion for any text ROI."""
        assert _is_occluded_by_engines(text_roi, []) is False

    @given(
        text_roi=roi_rect_strategy(),
        engine_bboxes=st.lists(roi_rect_strategy(), min_size=1, max_size=5),
    )
    @settings(max_examples=100)
    def test_occlusion_true_iff_any_bbox_intersects(self, text_roi, engine_bboxes):
        """_is_occluded_by_engines is True iff at least one bbox intersects text_roi."""
        any_intersects = any(
            _rects_intersect(text_roi, bbox) for bbox in engine_bboxes
        )
        assert _is_occluded_by_engines(text_roi, engine_bboxes) == any_intersects


# ─── Property 8: Numeric and Time Parsing Correctness ─────────────────────────


class TestPropertyParsing:
    """Property 8: Numeric and Time Parsing Correctness.

    For any valid numeric string (integer or decimal), parsing SHALL produce a
    float equal to the original value. For any valid mission elapsed time string
    matching the pattern T[+-]HH:MM:SS, parsing SHALL produce a correctly
    structured time value that round-trips back to the original string.

    **Validates: Requirements 4.4, 4.5**
    """

    @given(data=valid_integer_string())
    @settings(max_examples=100)
    def test_integer_parsing_produces_correct_float(self, data):
        """Parsing any valid integer string produces the equivalent float value."""
        text, expected = data
        result = _parse_numeric_value(text)
        assert result is not None
        assert result == expected

    @given(data=valid_decimal_string())
    @settings(max_examples=100)
    def test_decimal_parsing_produces_correct_float(self, data):
        """Parsing any valid decimal string produces the equivalent float value."""
        text, expected = data
        result = _parse_numeric_value(text)
        assert result is not None
        assert abs(result - expected) < 1e-9

    @given(time_str=valid_time_string())
    @settings(max_examples=100)
    def test_time_parsing_round_trips(self, time_str):
        """Parsing a valid T+HH:MM:SS / T-HH:MM:SS string round-trips to the same string."""
        result = _parse_time_value(time_str)
        assert result is not None
        assert result == time_str

    @given(time_str=valid_time_string())
    @settings(max_examples=100)
    def test_time_parsing_preserves_sign(self, time_str):
        """Parsed time string preserves the original sign (+/-)."""
        result = _parse_time_value(time_str)
        assert result is not None
        # The sign is at index 1 (after 'T')
        assert result[1] == time_str[1]

    @given(time_str=valid_time_string())
    @settings(max_examples=100)
    def test_time_parsing_produces_valid_format(self, time_str):
        """Parsed result always matches T[+-]HH:MM:SS format."""
        import re
        result = _parse_time_value(time_str)
        assert result is not None
        assert re.match(r"^T[+\-]\d{2}:\d{2}:\d{2}$", result)

    @given(
        integer_part=st.integers(min_value=0, max_value=9999),
        decimal_part=st.integers(min_value=0, max_value=99),
    )
    @settings(max_examples=100)
    def test_comma_decimal_parsed_same_as_dot_decimal(self, integer_part, decimal_part):
        """Comma-separated decimals are parsed identically to dot-separated ones."""
        dot_str = f"{integer_part}.{decimal_part}"
        comma_str = f"{integer_part},{decimal_part}"
        dot_result = _parse_numeric_value(dot_str)
        comma_result = _parse_numeric_value(comma_str)
        assert dot_result is not None
        assert comma_result is not None
        assert abs(dot_result - comma_result) < 1e-9
