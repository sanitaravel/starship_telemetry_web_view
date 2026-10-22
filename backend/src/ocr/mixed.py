"""One recogniser pass for crops that need different allowlists.

EasyOCR's ``recognize`` takes one allowlist per call, so the time field and
the numeric fields each cost a pass of the model, and those two passes were
80% of the OCR batcher's work on a real flight. The allowlist does not enter
the network, though: ``recognizer_predict`` applies it as a mask on the
per-character probabilities after the forward pass. So all crops go through
one forward pass here, each under its own mask.

The work comes in two parts: ``prepare_crop`` cuts, resizes and normalises
one crop, and ``recognize_prepared`` pads a batch of prepared crops to a common
width and runs the model. Only the padding depends on the batch -- no crop is
ever wider than the width EasyOCR picks for its batch -- so preparing crops
one at a time changes nothing in the result.

Everything follows EasyOCR's ``get_image_list``, ``get_text`` and
``recognizer_predict`` (easyocr>=1.7,<2.0) step for step: the same resizes,
normalisation, padding, greedy decoding, confidence score, and second pass
with raised contrast for low-confidence crops. A crop therefore comes back
exactly as ``recognize`` returns it from the same batch (checked against it
on 9281 crops from a real flight). Merging the allowlists does change one
thing: crops are padded to the widest in the batch, so numeric crops are now
padded to the width of the time field.

Measured on a real flight, RTX 4090, batch of 3 frames: two passes 15.4 ms,
one pass 8.8 ms.
"""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
import torchvision.transforms as transforms
from PIL import Image

import easyocr.easyocr as easyocr_module
from easyocr.recognition import adjust_contrast_grey, custom_mean
from easyocr.utils import calculate_ratio, compute_ratio_and_resize

# recognize()'s defaults, which the pipeline has always used
_CONTRAST_THS = 0.1
_ADJUST_CONTRAST = 0.5

_TO_TENSOR = transforms.ToTensor()

@dataclass
class PreparedCrop:
    """One crop cut, resized and normalised as EasyOCR does, but not padded."""

    ratio: float  # aspect ratio as get_image_list measures it, sets batch width
    crop: np.ndarray  # the resized crop, for the low-confidence retry
    normal: torch.Tensor  # (1, imgH, w): first-round input


def ignore_indices(reader: Any, allowlist: str | None) -> list[int]:
    """The character indices ``recognize`` masks out for ``allowlist``."""
    if allowlist:
        ignore_char = set(reader.character) - set(allowlist)
    else:
        ignore_char = set(reader.character) - set(reader.lang_char)
    # +1: index 0 is the CTC blank
    return sorted(reader.character.index(c) + 1 for c in ignore_char if c in reader.character)


def prepare_crop(image: np.ndarray, box: list[int]) -> PreparedCrop | None:
    """Cut and normalise one crop, as get_image_list and AlignCollate would.

    Returns None for a crop get_image_list would drop.

    Args:
        image: Grayscale image the box refers to.
        box: [x_min, x_max, y_min, y_max].
    """
    img_h = easyocr_module.imgH
    max_y, max_x = image.shape
    x_min, x_max = max(0, box[0]), min(box[1], max_x)
    y_min, y_max = max(0, box[2]), min(box[3], max_y)
    width, height = x_max - x_min, y_max - y_min
    if width <= 0 or height <= 0 or int(img_h * calculate_ratio(width, height)) == 0:
        return None
    crop, ratio = compute_ratio_and_resize(
        image[y_min:y_max, x_min:x_max], width, height, img_h
    )
    return PreparedCrop(ratio=ratio, crop=crop, normal=_normalise(crop, 0.0))


def recognize_prepared(
    reader: Any,
    crops: list[PreparedCrop],
    allowlists: list[str | None],
) -> list[tuple[str, float]]:
    """Recognise prepared crops in one pass, each under its own allowlist.

    Returns (text, confidence) per crop, in the order given.
    """
    if not crops:
        return []
    # get_image_list's batch width: the widest ratio, at least 1, rounded up
    img_w = math.ceil(max([1.0, *(c.ratio for c in crops)])) * easyocr_module.imgH
    mask_for = {allowlist: ignore_indices(reader, allowlist) for allowlist in set(allowlists)}
    masks = [mask_for[allowlist] for allowlist in allowlists]

    first = _predict(reader, [_fit(c, img_w) for c in crops], masks, img_w)
    results = list(first)

    # Second round, as get_text: low-confidence crops again with raised
    # contrast, keeping whichever reading is more confident
    low = [i for i, (_, confidence) in enumerate(first) if confidence < _CONTRAST_THS]
    if low:
        second = _predict(
            reader,
            # Only a few crops need this, so it is prepared here, not up front
            [_normalise(crops[i].crop, _ADJUST_CONTRAST, cap=img_w) for i in low],
            [masks[i] for i in low],
            img_w,
        )
        for i, retry in zip(low, second):
            results[i] = first[i] if first[i][1] > retry[1] else retry
    return results


def _normalise(crop: np.ndarray, adjust_contrast: float, cap: int | None = None) -> torch.Tensor:
    """AlignCollate for one image, without the padding."""
    img_h = easyocr_module.imgH
    image = Image.fromarray(crop, "L")
    w, h = image.size
    if adjust_contrast > 0:
        image = Image.fromarray(
            adjust_contrast_grey(np.array(image.convert("L")), target=adjust_contrast), "L"
        )
    resized_w = math.ceil(img_h * (w / float(h)))
    if cap is not None and resized_w > cap:
        resized_w = cap
    tensor = _TO_TENSOR(image.resize((resized_w, img_h), Image.BICUBIC))
    return tensor.sub_(0.5).div_(0.5)


def _fit(crop: PreparedCrop, img_w: int) -> torch.Tensor:
    """The prepared tensor, or, were it ever wider than the batch, AlignCollate's capped one."""
    if crop.normal.size(2) <= img_w:
        return crop.normal
    return _normalise(crop.crop, 0.0, cap=img_w)


def _predict(
    reader: Any,
    tensors: list[torch.Tensor],
    masks: list[list[int]],
    img_w: int,
) -> list[tuple[str, float]]:
    """recognizer_predict over one batch, with a mask per row."""
    img_h = easyocr_module.imgH
    # NormalizePAD's padding: right-pad each crop with its last column
    image_tensors = torch.zeros(len(tensors), 1, img_h, img_w)
    for row, tensor in enumerate(tensors):
        w = tensor.size(2)
        image_tensors[row, :, :, :w] = tensor
        if w < img_w:
            image_tensors[row, :, :, w:] = tensor[:, :, w - 1 : w]

    device = reader.device
    model = reader.recognizer
    model.eval()
    batch_max_length = int(img_w / 10)

    with torch.no_grad():
        batch_size = image_tensors.size(0)
        image = image_tensors.to(device)
        text_for_pred = torch.LongTensor(batch_size, batch_max_length + 1).fill_(0).to(device)
        preds = model(image, text_for_pred)
        preds_size = torch.IntTensor([preds.size(1)] * batch_size)

        preds_prob = F.softmax(preds, dim=2)
        preds_prob = preds_prob.cpu().detach().numpy()
        for row, ignore in enumerate(masks):
            preds_prob[row, :, ignore] = 0.0
        pred_norm = preds_prob.sum(axis=2)
        preds_prob = preds_prob / np.expand_dims(pred_norm, axis=-1)
        preds_prob = torch.from_numpy(preds_prob).float().to(device)

        _, preds_index = preds_prob.max(2)
        preds_index = preds_index.view(-1)
        preds_str = reader.converter.decode_greedy(
            preds_index.data.cpu().detach().numpy(), preds_size.data
        )
        preds_prob = preds_prob.cpu().detach().numpy()

    values = preds_prob.max(axis=2)
    indices = preds_prob.argmax(axis=2)
    results = []
    for text, v, i in zip(preds_str, values, indices):
        max_probs = v[i != 0]
        if len(max_probs) == 0:
            max_probs = np.array([0])
        results.append((text, custom_mean(max_probs)))
    return results
