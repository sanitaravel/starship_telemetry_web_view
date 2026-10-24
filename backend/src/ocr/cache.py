"""Result cache for static ROIs, so unchanged text is not read again.

Each cacheable field's result is stored with a fingerprint of its ROI and the
frame it was read on, and reused while later frames show the same text.
"""

import threading

import cv2
import numpy as np

from src.ocr.regions import ink_level
from src.ocr.results import OCRFieldResult


# Fields whose text is static for long stretches of a flight: the unit labels,
# the stage names and the HUD milestone arc's label spots (empty for minutes
# at a time). Their ROIs are also the widest, and the recogniser's cost scales with ROI width rather than with how many ROIs are in the batch,
# so re-reading them on every frame is where most of the OCR time went. They
# are read once and then reused until their pixels change.
CACHEABLE_FIELDS = frozenset({
    "speed_l_unit",
    "speed_r_unit",
    "altitude_l_unit",
    "altitude_r_unit",
    "stage_l",
    "stage_r",
    "stage_sep_text",
    "timeline_below",
})

# The change detector reduces an ROI to the fraction of ink in each cell of a
# rows x columns grid, and compares two of those by their L1 difference
# relative to the ink they hold (see signatures_match).
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
# signature has not changed, so a signature collision cannot persist. Read
# from the module at lookup time, so tests can patch it.
CACHE_REFRESH_FRAMES = 150


def roi_signature(
    gray: np.ndarray, box: tuple[int, int, int, int]
) -> np.ndarray | None:
    """Return a noise-tolerant fingerprint of what is written in an ROI.

    The crop is reduced to ink / no ink with the same level as
    ``regions.text_columns``, which drops the background showing through the
    overlay, and then averaged down to a fixed grid, which smooths out the
    letter edges that flicker between compressed frames. Compare two
    fingerprints with ``signatures_match``. Returns None for an empty crop.
    """
    x1, y1, x2, y2 = box
    crop = gray[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    level = ink_level(crop)
    ink = (crop > level).astype(np.float32)
    rows, cols = _SIGNATURE_GRID
    return cv2.resize(ink, (cols, rows), interpolation=cv2.INTER_AREA)


def signatures_match(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether two ROI fingerprints show the same text.

    The difference is taken relative to the ink in the fuller of the two, so
    it reads the same for thin and bold text: 0 for identical text, 1 for text
    against a blank ROI. Two blank ROIs match.
    """
    ink = max(float(a.sum()), float(b.sum()), 1.0)
    return float(np.abs(a - b).sum()) / ink < _SIGNATURE_MAX_DIFF


class FieldCache:
    """Recognised results of static ROIs, keyed by field name.

    Shared by the OCR worker threads, so every access takes its own lock. The
    frame index it hands out counts frames rather than recognition passes, and
    sets the refresh interval.
    """

    def __init__(self) -> None:
        # field name -> (ROI signature, result, frame index it was read on)
        self._entries: dict[str, tuple[np.ndarray, OCRFieldResult, int]] = {}
        self._lock = threading.Lock()
        self._frame_counter: int = 0

    def next_frame_index(self) -> int:
        """Allocate this frame's index for the cache refresh interval."""
        with self._lock:
            self._frame_counter += 1
            return self._frame_counter

    def lookup(
        self, field_name: str, signature: np.ndarray | None, frame_index: int
    ) -> OCRFieldResult | None:
        """Return the stored result for an unchanged ROI, or None to read it.

        A cached field is re-read when its signature changes and at least every
        ``CACHE_REFRESH_FRAMES`` frames regardless, so a stale entry cannot
        outlive a change the signature failed to register.
        """
        if signature is None:
            return None
        with self._lock:
            entry = self._entries.get(field_name)
            if entry is not None:
                cached_signature, result, stored_at = entry
                if (
                    signatures_match(cached_signature, signature)
                    and frame_index - stored_at < CACHE_REFRESH_FRAMES
                ):
                    return result
            return None

    def store(
        self,
        field_name: str,
        signature: np.ndarray,
        result: OCRFieldResult,
        frame_index: int,
    ) -> None:
        """Remember a freshly recognised result against its ROI signature."""
        with self._lock:
            self._entries[field_name] = (signature, result, frame_index)

    def clear(self) -> None:
        """Forget every entry and restart the frame count, for a new run."""
        with self._lock:
            self._entries.clear()
            self._frame_counter = 0
