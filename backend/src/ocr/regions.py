"""ROI geometry and pixel helpers: clamping, cropping, grayscale and ink."""

import cv2
import numpy as np

from src.models import ROIRect


# Unit and value ROIs are much wider than their text, and EasyOCR misreads
# short strings in a lot of padding: "KM" as garbage, and "2.7" km as "22.7"
# (the leading digit doubled). Their boxes are narrowed to the text first.
# Text pixels are those at least this bright (absolute floor, and relative to
# the brightest pixel in the ROI).
_INK_MIN_LEVEL = 120
_INK_RELATIVE_LEVEL = 0.6
_INK_PADDING = 3


def roi_bounds(frame: np.ndarray, roi: ROIRect) -> tuple[int, int, int, int]:
    """Return (x1, y1, x2, y2) for the ROI, clamped to the frame boundaries."""
    h, w = frame.shape[:2]

    x1 = max(0, int(roi.x))
    y1 = max(0, int(roi.y))
    x2 = min(w, int(roi.x + roi.width))
    y2 = min(h, int(roi.y + roi.height))

    return x1, y1, x2, y2


def crop_frame_to_roi(frame: np.ndarray, roi: ROIRect) -> np.ndarray:
    """Crop the frame to the given ROI rectangle.

    Clamps coordinates to frame boundaries to avoid out-of-bounds access.
    """
    x1, y1, x2, y2 = roi_bounds(frame, roi)
    return frame[y1:y2, x1:x2]


def to_gray(image: np.ndarray) -> np.ndarray:
    """Convert a BGR image to single-channel grayscale (no-op if already gray)."""
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def ink_level(crop: np.ndarray) -> float:
    """The brightness above which a pixel of a non-empty crop counts as text."""
    return max(_INK_MIN_LEVEL, int(crop.max()) * _INK_RELATIVE_LEVEL)


def text_columns(gray: np.ndarray, x1: int, x2: int, y1: int, y2: int) -> tuple[int, int] | None:
    """Narrow a unit or value ROI's horizontal extent to its bright text pixels.

    Returns the padded (x1, x2) span of the text, or None if the ROI holds
    no text-bright pixels at all.
    """
    crop = gray[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    level = ink_level(crop)
    columns = np.flatnonzero((crop > level).any(axis=0))
    if columns.size == 0:
        return None
    return (
        max(x1, x1 + int(columns[0]) - _INK_PADDING),
        min(x2, x1 + int(columns[-1]) + 1 + _INK_PADDING),
    )
