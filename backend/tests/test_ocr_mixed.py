"""Tests for one-pass recognition with a per-crop allowlist (src.ocr_mixed).

These use a small deterministic stand-in for the recogniser network and
EasyOCR's own converter and preprocessing, so they need neither the model
weights nor a GPU. The real model was checked separately: on 9281 crops from a
real flight, one-pass recognition matched EasyOCR's recognize exactly.
"""

import numpy as np
import pytest
import torch
import torch.nn.functional as F

import easyocr.easyocr as easyocr_module
from easyocr.recognition import get_text
from easyocr.utils import CTCLabelConverter, get_image_list

from src.ocr_mixed import ignore_indices, prepare_crop, recognize_prepared


CHARACTERS = "0123456789:.abcKMH/"


class ImageNet(torch.nn.Module):
    """Logits that depend on the image: pooled columns through fixed weights."""

    def __init__(self, classes: int) -> None:
        super().__init__()
        gen = torch.Generator().manual_seed(0)
        self.weights = torch.randn(4, classes, generator=gen) * 3

    def forward(self, image, text):
        pooled = F.adaptive_avg_pool2d(image, (4, 8))  # (B, 1, 4, 8)
        steps = pooled.squeeze(1).transpose(1, 2)  # (B, 8 timesteps, 4)
        return steps @ self.weights


class FixedNet(torch.nn.Module):
    """Prefers 'a', then '5', at every timestep, whatever the image."""

    def __init__(self, classes: int) -> None:
        super().__init__()
        self.logits = torch.full((classes,), -5.0)
        self.logits[1 + CHARACTERS.index("a")] = 6.0
        self.logits[1 + CHARACTERS.index("5")] = 4.0

    def forward(self, image, text):
        return self.logits.expand(image.size(0), 6, -1).clone()


class FakeReader:
    def __init__(self, net) -> None:
        self.character = CHARACTERS
        self.lang_char = CHARACTERS
        self.device = "cpu"
        self.converter = CTCLabelConverter(CHARACTERS)
        self.recognizer = net(len(self.converter.character))


def make_image(seed: int = 0):
    """A grayscale canvas with several crops of varying content and width."""
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 60, (240, 400), dtype=np.uint8)
    boxes = []
    for i, width in enumerate([120, 60, 300, 90, 40]):
        y = 10 + i * 45
        image[y:y + 35, 10:10 + width] = rng.integers(60, 255, (35, width), dtype=np.uint8)
        boxes.append([10, 10 + width, y, y + 35])
    return image, boxes


def recognize_mixed(reader, image, boxes, allowlists):
    """(corners, text, confidence) per kept box, in recognize's box format."""
    kept = [(box, allowlist, prepare_crop(image, box)) for box, allowlist in zip(boxes, allowlists)]
    kept = [k for k in kept if k[2] is not None]
    readings = recognize_prepared(reader, [c for _, _, c in kept], [a for _, a, _ in kept])
    return [([[b[0], b[2]]], text, conf) for (b, _, _), (text, conf) in zip(kept, readings)]


def by_corner(predictions):
    return {(int(b[0][0]), int(b[0][1])): (text, float(conf)) for b, text, conf in predictions}


@pytest.mark.parametrize("allowlist", [None, "0123456789:", "abcKMH/"])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_matches_easyocr_for_a_single_allowlist(allowlist, seed):
    # With every crop under the same allowlist, the result must be exactly
    # what EasyOCR's own batched path (get_text) returns.
    reader = FakeReader(ImageNet)
    image, boxes = make_image(seed)

    ours = by_corner(recognize_mixed(reader, image, boxes, [allowlist] * len(boxes)))

    image_list, max_width = get_image_list(boxes, [], image, model_height=easyocr_module.imgH)
    ignore = "".join(reader.converter.character[i] for i in ignore_indices(reader, allowlist))
    theirs = by_corner(get_text(
        reader.character, easyocr_module.imgH, int(max_width), reader.recognizer,
        reader.converter, image_list, ignore, batch_size=len(image_list), workers=0,
        device="cpu",
    ))

    assert ours.keys() == theirs.keys()
    for corner in ours:
        assert ours[corner][0] == theirs[corner][0]
        assert ours[corner][1] == pytest.approx(theirs[corner][1], abs=1e-6)


def test_each_crop_gets_its_own_allowlist():
    reader = FakeReader(FixedNet)
    image, boxes = make_image()
    allowlists = ["0123456789", None, "abc", "0123456789", "KMH"]

    got = by_corner(recognize_mixed(reader, image, boxes, allowlists))
    texts = [got[(b[0], b[2])][0] for b in boxes]

    # 'a' wins unless the crop's allowlist masks it out; then '5', and with
    # neither allowed, whatever the best remaining character is
    assert texts[0] == "5"
    assert texts[1] == "a"
    assert texts[2] == "a"
    assert texts[3] == "5"
    assert texts[4] not in ("a", "5") and set(texts[4]) <= set("KMH")


def test_ignore_indices_follow_easyocr():
    reader = FakeReader(FixedNet)
    digits = ignore_indices(reader, "0123456789")
    masked = {reader.converter.character[i] for i in digits}

    assert masked == set(CHARACTERS) - set("0123456789")
    assert ignore_indices(reader, None) == []  # lang_char covers every character


def test_no_boxes_returns_nothing():
    reader = FakeReader(FixedNet)

    assert recognize_mixed(reader, np.zeros((50, 50), np.uint8), [], []) == []
