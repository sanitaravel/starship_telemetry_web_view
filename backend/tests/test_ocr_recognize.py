"""Tests for the recognition-only OCR path of EasyOCREngine (fixed ROI boxes).

The engine hands its ROIs to the batcher (src.ocr.batcher), which is what gets
substituted here: it receives boxes in frame coordinates and answers in the
order it was given them, so these tests exercise the engine's cropping,
grouping, caching and parsing without a model. The batcher's own batching is
covered in test_ocr_batcher.py.
"""

from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from src.core.enums import OCRFieldStatus
from src.core.gpu import AccelerationBackend, GPUCapabilities
from src.core.models import ROIRect
from src.ocr.engine import EasyOCREngine, configure_torch_threads


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

# Text drawn in each ROI, keyed by the ROI's top-left corner in the frame
TEXT_AT = {
    (800, 10): "00:00:00",
    (50, 900): "1234",
    (1700, 900): "5678",
    (50, 940): "KM/H",
    (100, 700): "SHIP",
}

# The boxes the engine derives from REGIONS on a uniformly lit frame
TIME_BOX = [800, 1120, 10, 60]
SPEED_L_BOX = [50, 250, 900, 940]
UNIT_BOX = [50, 150, 940, 970]
STAGE_BOX = [100, 300, 700, 740]


class FakeBatcher:
    """Stands in for BatchedRecognizer, answering from TEXT_AT in box order."""

    def __init__(self, confidence: float = 0.95, drop: tuple[str, ...] = ()):
        self.calls: list[list[tuple[list[int], str | None]]] = []
        self._confidence = confidence
        self._drop = drop

    def recognize(self, image, items):
        self.calls.append([(list(box), allowlist) for box, allowlist in items])
        predictions = []
        for (x_min, x_max, y_min, y_max), _allowlist in items:
            text = TEXT_AT.get((x_min, y_min))
            if text is None or text in self._drop:
                predictions.append(None)
            else:
                predictions.append(([[x_min, y_min]], text, self._confidence))
        return predictions

    @property
    def boxes(self) -> list[list[int]]:
        """Every box handed to the recogniser, across all calls."""
        return [box for call in self.calls for box, _ in call]

    @property
    def allowlists(self) -> list[str | None]:
        """The allowlist asked for each box, across all calls."""
        return [allowlist for call in self.calls for _, allowlist in call]


def build_engine(batcher: FakeBatcher | None = None, **kwargs) -> EasyOCREngine:
    """An engine on the recognition path with its batcher substituted."""
    with patch("src.ocr.engine.easyocr.Reader"):
        engine = EasyOCREngine(CPU, use_detection=False, **kwargs)
    engine._batcher = batcher or FakeBatcher()
    return engine


@pytest.fixture
def frame() -> np.ndarray:
    return np.full((1080, 1920, 3), 128, dtype=np.uint8)


def test_predictions_map_back_to_their_fields(frame):
    engine = build_engine()

    result = engine.extract_text(frame, REGIONS)

    assert result.time.parsed_value == "T+00:00:00"
    assert result.speed_l.parsed_value == 1234.0
    assert result.speed_r.parsed_value == 5678.0
    assert result.speed_l_unit.parsed_value == "KM/H"
    assert result.stage_l.parsed_value == "SHIP"


def test_every_roi_of_a_frame_goes_over_in_one_request(frame):
    batcher = FakeBatcher()
    engine = build_engine(batcher)
    engine._t_zero_detected = True  # past the T-0 gate

    engine.extract_text(frame, REGIONS)

    # One request, carrying each ROI with the allowlist it needs
    assert len(batcher.calls) == 1
    assert len(batcher.calls[0]) == len(REGIONS)
    assert TIME_BOX in batcher.boxes and STAGE_BOX in batcher.boxes
    assert len(set(batcher.allowlists)) == 4  # time / numeric / unit / labels


def test_only_the_time_roi_is_read_before_t_zero(frame):
    batcher = FakeBatcher()
    engine = build_engine(batcher)

    # The clock has not reached 00:00:00 yet, so no telemetry is emitted
    with patch.dict(TEXT_AT, {(800, 10): "00:01:23"}):
        result = engine.extract_text(frame, REGIONS)

    assert batcher.boxes == [TIME_BOX]
    assert result.time.status == OCRFieldStatus.UNAVAILABLE
    assert result.speed_l.status == OCRFieldStatus.UNAVAILABLE
    assert not engine.t_zero_detected


def test_t_zero_frame_reads_the_time_roi_once(frame):
    batcher = FakeBatcher()
    engine = build_engine(batcher)

    result = engine.extract_text(frame, REGIONS)

    # the gate's 00:00:00 is kept, not read again in a later batch
    assert batcher.boxes.count(TIME_BOX) == 1
    assert result.time.parsed_value == "T+00:00:00"
    assert result.speed_l.parsed_value == 1234.0
    assert engine.t_zero_detected


def test_fields_are_read_without_a_time_roi(frame):
    batcher = FakeBatcher()
    engine = build_engine(batcher)

    result = engine.extract_text(frame, {"speed_l": REGIONS["speed_l"]})

    assert batcher.boxes == [SPEED_L_BOX]
    assert result.speed_l.parsed_value == 1234.0


def test_grayscale_frame_is_passed_to_the_batcher(frame):
    images = []

    class RecordingBatcher(FakeBatcher):
        def recognize(self, image, items):
            images.append(image)
            return super().recognize(image, items)

    engine = build_engine(RecordingBatcher())
    engine.extract_text(frame, REGIONS)

    assert images and all(image.ndim == 2 for image in images)


def test_missing_or_low_confidence_prediction_is_unavailable(frame):
    # speed_r gets no prediction at all; the stage label comes back unsure
    engine = build_engine(FakeBatcher(drop=("5678",)))
    low = build_engine(FakeBatcher(confidence=0.1))

    result = engine.extract_text(frame, REGIONS)
    low_result = low.extract_text(frame, REGIONS)

    assert result.speed_l.status == OCRFieldStatus.AVAILABLE
    assert result.speed_r.status == OCRFieldStatus.UNAVAILABLE
    assert low_result.stage_l.status == OCRFieldStatus.UNAVAILABLE


def test_value_box_narrowed_to_its_text():
    # A short value ("2.7") centred in a wide ROI: recognising the whole box
    # made EasyOCR double the leading digit ("22.7")
    frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)
    frame[905:935, 140:170] = 230  # text inside speed_l (x 50..250)
    batcher = FakeBatcher()
    engine = build_engine(batcher)

    engine.extract_text(frame, {"speed_l": REGIONS["speed_l"]})

    assert batcher.boxes == [[137, 173, 900, 940]]


@patch("src.ocr.engine.easyocr.Reader")
def test_detection_mode_uses_readtext(reader_cls: MagicMock, frame):
    reader = reader_cls.return_value
    reader.readtext.return_value = [([[0, 0]], "00:00:00", 0.9)]
    engine = EasyOCREngine(CPU, use_detection=True)

    engine.extract_text(frame, {"time": REGIONS["time"]})

    reader.readtext.assert_called_once()
    reader.recognize.assert_not_called()


@patch("src.ocr.engine.easyocr.Reader")
def test_detection_mode_from_environment(reader_cls: MagicMock, monkeypatch):
    monkeypatch.setenv("OCR_USE_DETECTION", "1")
    assert EasyOCREngine(CPU)._use_detection is True
    monkeypatch.delenv("OCR_USE_DETECTION")
    assert EasyOCREngine(CPU)._use_detection is False


def test_reset_clears_t_zero(frame):
    engine = build_engine()
    engine.extract_text(frame, {"time": REGIONS["time"]})
    assert engine.t_zero_detected
    engine.reset()
    assert not engine.t_zero_detected


def test_torch_threads_capped_by_default_and_overridable(monkeypatch):
    calls = []
    monkeypatch.setattr("src.ocr.engine.torch.set_num_threads", calls.append)
    monkeypatch.delenv("OCR_TORCH_THREADS", raising=False)
    configure_torch_threads()
    monkeypatch.setenv("OCR_TORCH_THREADS", "3")
    configure_torch_threads()
    monkeypatch.setenv("OCR_TORCH_THREADS", "not a number")
    configure_torch_threads()
    assert calls == [1, 3, 1]


@patch("src.ocr.engine.easyocr.Reader")
def test_torch_threads_capped_on_gpu_too(reader_cls: MagicMock, monkeypatch):
    # On GPU torch's OpenMP pool only spins between ops, taking CPU from the
    # video decoders (see configure_torch_threads)
    calls = []
    monkeypatch.setattr("src.ocr.engine.torch.set_num_threads", calls.append)
    monkeypatch.delenv("OCR_TORCH_THREADS", raising=False)
    gpu = GPUCapabilities(
        backend=AccelerationBackend.CUDA_GPU, device_name="test", cuda_version="12.1",
        gpu_available=True,
    )

    EasyOCREngine(gpu, use_detection=False)

    assert calls == [1]


# --- static-ROI cache (unit labels and stage names) ---


def test_static_fields_are_not_reread_while_unchanged(frame):
    batcher = FakeBatcher()
    engine = build_engine(batcher)

    first = engine.extract_text(frame, REGIONS)
    batcher.calls.clear()
    second = engine.extract_text(frame, REGIONS)

    # The cached values are still reported, without going through the model
    assert second.stage_l.parsed_value == first.stage_l.parsed_value == "SHIP"
    assert second.speed_l_unit.parsed_value == first.speed_l_unit.parsed_value == "KM/H"
    assert UNIT_BOX not in batcher.boxes
    assert STAGE_BOX not in batcher.boxes
    # The changing values are read every frame
    assert SPEED_L_BOX in batcher.boxes
    assert TIME_BOX in batcher.boxes


def test_changed_stage_label_is_reread(frame):
    batcher = FakeBatcher()
    engine = build_engine(batcher)

    engine.extract_text(frame, REGIONS)
    changed = frame.copy()
    changed[700:740, 100:300] = 20  # the stage_l ROI goes dark
    batcher.calls.clear()
    engine.extract_text(changed, REGIONS)

    assert STAGE_BOX in batcher.boxes
    assert UNIT_BOX not in batcher.boxes  # unit label still cached


def test_signature_tolerates_compression_noise():
    bright = np.full((1080, 1920, 3), 200, dtype=np.uint8)
    batcher = FakeBatcher()
    engine = build_engine(batcher)

    engine.extract_text(bright, REGIONS)
    rng = np.random.default_rng(0)
    noisy = np.clip(
        bright.astype(np.int16) + rng.integers(-8, 9, bright.shape), 0, 255
    ).astype(np.uint8)
    batcher.calls.clear()
    engine.extract_text(noisy, REGIONS)

    assert STAGE_BOX not in batcher.boxes


def test_cached_field_is_refreshed_periodically(frame, monkeypatch):
    monkeypatch.setattr("src.ocr.cache.CACHE_REFRESH_FRAMES", 3)
    batcher = FakeBatcher()
    engine = build_engine(batcher)

    engine.extract_text(frame, REGIONS)  # read and stored
    engine.extract_text(frame, REGIONS)  # cached
    batcher.calls.clear()
    engine.extract_text(frame, REGIONS)  # still cached
    assert STAGE_BOX not in batcher.boxes

    batcher.calls.clear()
    engine.extract_text(frame, REGIONS)  # refresh interval elapsed
    assert STAGE_BOX in batcher.boxes


def test_reset_clears_the_roi_cache(frame):
    batcher = FakeBatcher()
    engine = build_engine(batcher)

    engine.extract_text(frame, REGIONS)
    engine.reset()
    batcher.calls.clear()
    engine.extract_text(frame, REGIONS)

    assert STAGE_BOX in batcher.boxes


# --- change detection on realistic text ---
# Real overlays put thin text over moving, compressed video. The detector must
# hold still through that noise and still notice when the text changes.

FONTS = [pytest.param(1.0, 2, id="bold"), pytest.param(0.6, 1, id="thin")]


def labelled_frame(text: str, scale: float, thickness: int, sigma: float = 0.0, seed: int = 0):
    """A dark frame with ``text`` in the stage_l ROI, plus optional noise."""
    frame = np.full((1080, 1920, 3), 25, dtype=np.uint8)
    cv2.putText(frame, text, (104, 732), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (240, 240, 240), thickness, cv2.LINE_AA)
    if sigma:
        rng = np.random.default_rng(seed)
        noisy = frame.astype(np.float32) + rng.normal(0, sigma, frame.shape)
        frame = np.clip(noisy, 0, 255).astype(np.uint8)
    return frame


@pytest.mark.parametrize("scale,thickness", FONTS)
def test_label_under_heavy_noise_stays_cached(scale, thickness):
    batcher = FakeBatcher()
    engine = build_engine(batcher)
    regions = {"stage_l": REGIONS["stage_l"]}

    engine.extract_text(labelled_frame("SHIP", scale, thickness, 25, seed=0), regions)
    for seed in range(1, 15):
        batcher.calls.clear()
        engine.extract_text(labelled_frame("SHIP", scale, thickness, 25, seed), regions)
        assert STAGE_BOX not in batcher.boxes, f"re-read on noise seed {seed}"


@pytest.mark.parametrize("scale,thickness", FONTS)
@pytest.mark.parametrize(
    "before,after",
    [("SHIP", "BOOSTER"), ("KM", "MI"), ("KM/H", "MPH"),
     ("STARSHIP", "SUPER HEAVY"), ("", "STAGE SEP"), ("STAGE SEP", "")],
)
def test_every_label_change_is_detected(before, after, scale, thickness):
    batcher = FakeBatcher()
    engine = build_engine(batcher)
    regions = {"stage_l": REGIONS["stage_l"]}

    engine.extract_text(labelled_frame(before, scale, thickness), regions)
    batcher.calls.clear()
    engine.extract_text(labelled_frame(after, scale, thickness), regions)

    assert STAGE_BOX in batcher.boxes
