"""Tests for the recognition-only OCR path of EasyOCREngine (fixed ROI boxes)."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.enums import OCRFieldStatus
from src.gpu_detector import AccelerationBackend, GPUCapabilities
from src.models import ROIRect
from src.ocr_engine import EasyOCREngine, configure_torch_threads


CPU = GPUCapabilities(
    backend=AccelerationBackend.CPU, device_name=None, cuda_version=None, gpu_available=False
)

REGIONS = {
    "time": ROIRect(id="time", x=800, y=10, width=320, height=50),
    "speed_l": ROIRect(id="speed_l", x=50, y=900, width=200, height=40),
    "speed_r": ROIRect(id="speed_r", x=1700, y=900, width=200, height=40),
    "speed_l_unit": ROIRect(id="speed_l_unit", x=50, y=940, width=100, height=30),
    "stage_l": ROIRect(id="stage_l", x=100, y=700, width=200, height=40),
}

# Text drawn in each ROI, keyed by the ROI's top-left corner
TEXT_AT = {
    (50, 900): "1234",
    (1700, 900): "5678",
    (50, 940): "KM/H",
    (100, 700): "SHIP",
}


def fake_recognize(image, horizontal_list, free_list, **kwargs):
    """Mimic EasyOCR: one (box, text, conf) per box, in reversed order."""
    if image.shape == (50, 320):  # the cropped time ROI
        return [([[0, 0], [320, 0], [320, 50], [0, 50]], "00:00:00", 0.9)]
    results = []
    for x_min, x_max, y_min, y_max in horizontal_list:
        box = [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]
        results.append((box, TEXT_AT[(x_min, y_min)], 0.95))
    return list(reversed(results))


@pytest.fixture
def frame() -> np.ndarray:
    return np.full((1080, 1920, 3), 128, dtype=np.uint8)


@patch("src.ocr_engine.easyocr.Reader")
def test_recognize_maps_reordered_results_back_to_fields(reader_cls: MagicMock, frame):
    reader_cls.return_value.recognize.side_effect = fake_recognize
    engine = EasyOCREngine(CPU, use_detection=False)

    result = engine.extract_text(frame, REGIONS)

    assert result.time.parsed_value == "T+00:00:00"
    assert result.speed_l.parsed_value == 1234.0
    assert result.speed_r.parsed_value == 5678.0
    assert result.speed_l_unit.parsed_value == "KM/H"
    assert result.stage_l.parsed_value == "SHIP"
    reader_cls.return_value.readtext.assert_not_called()


@patch("src.ocr_engine.easyocr.Reader")
def test_one_recognize_call_per_allowlist_group(reader_cls: MagicMock, frame):
    reader = reader_cls.return_value
    reader.recognize.side_effect = fake_recognize
    engine = EasyOCREngine(CPU, use_detection=False)

    engine.extract_text(frame, REGIONS)

    # time (cropped) + numeric group + unit group + stage-label group
    assert reader.recognize.call_count == 4
    grouped = [c for c in reader.recognize.call_args_list if c.args[0].shape == (1080, 1920)]
    assert sorted(len(c.kwargs["horizontal_list"]) for c in grouped) == [1, 1, 2]
    # Grayscale goes in, so EasyOCR does no colour conversion of its own
    assert all(c.args[0].ndim == 2 for c in reader.recognize.call_args_list)


@patch("src.ocr_engine.easyocr.Reader")
def test_missing_or_low_confidence_prediction_is_unavailable(reader_cls: MagicMock, frame):
    def recognize(image, horizontal_list, free_list, **kwargs):
        results = fake_recognize(image, horizontal_list, free_list, **kwargs)
        # Drop speed_r's result and make the stage label low-confidence
        return [
            (box, text, 0.1 if text == "SHIP" else conf)
            for box, text, conf in results
            if text != "5678"
        ]

    reader_cls.return_value.recognize.side_effect = recognize
    engine = EasyOCREngine(CPU, use_detection=False)

    result = engine.extract_text(frame, REGIONS)

    assert result.speed_l.status == OCRFieldStatus.AVAILABLE
    assert result.speed_r.status == OCRFieldStatus.UNAVAILABLE
    assert result.stage_l.status == OCRFieldStatus.UNAVAILABLE


@patch("src.ocr_engine.easyocr.Reader")
def test_detection_mode_uses_readtext(reader_cls: MagicMock, frame):
    reader = reader_cls.return_value
    reader.readtext.return_value = [([[0, 0]], "00:00:00", 0.9)]
    engine = EasyOCREngine(CPU, use_detection=True)

    engine.extract_text(frame, {"time": REGIONS["time"]})

    reader.readtext.assert_called_once()
    reader.recognize.assert_not_called()


@patch("src.ocr_engine.easyocr.Reader")
def test_detection_mode_from_environment(reader_cls: MagicMock, monkeypatch):
    monkeypatch.setenv("OCR_USE_DETECTION", "1")
    assert EasyOCREngine(CPU)._use_detection is True
    monkeypatch.delenv("OCR_USE_DETECTION")
    assert EasyOCREngine(CPU)._use_detection is False


@patch("src.ocr_engine.easyocr.Reader")
def test_reset_clears_t_zero(reader_cls: MagicMock, frame):
    reader_cls.return_value.recognize.side_effect = fake_recognize
    engine = EasyOCREngine(CPU, use_detection=False)
    engine.extract_text(frame, {"time": REGIONS["time"]})
    assert engine.t_zero_detected
    engine.reset()
    assert not engine.t_zero_detected


def test_torch_threads_capped_on_cpu_only(monkeypatch):
    calls = []
    monkeypatch.setattr("src.ocr_engine.torch.set_num_threads", calls.append)
    monkeypatch.delenv("OCR_TORCH_THREADS", raising=False)
    configure_torch_threads(gpu_available=True)
    configure_torch_threads(gpu_available=False)
    monkeypatch.setenv("OCR_TORCH_THREADS", "3")
    configure_torch_threads(gpu_available=False)
    assert calls == [1, 3]
