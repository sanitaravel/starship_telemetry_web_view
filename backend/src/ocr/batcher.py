"""Cross-frame micro-batching for the EasyOCR recogniser.

The recogniser's cost is set by the padded width of a batch, not by how many
crops are in it: measured on an RTX 4090, one 256px crop costs 6.4 ms and 72 of
them cost 8.9 ms. Recognising each frame on its own therefore pays that fixed
cost once per frame however little text the frame holds, and spreading frames
over several OCR threads only queues them on one CUDA stream -- which is why
widening the OCR pool moved GPU utilisation without moving throughput.

BatchedRecognizer takes the ROIs of every frame currently in flight and, on
GPU, runs the model once over all of them, each crop under its own allowlist
(see ocr.mixed). The fixed cost is then shared by all the frames in the batch
and all their fields, instead of paid per frame and per allowlist.

Crops are padded to the widest one in their pass, so it is tempting to keep a
wide stage label from padding every short number out to its own length.
Measured on an RTX 4090 at batch 24, a pass costs about 4 ms plus 0.02 ms per
pixel of padded width (4.95 ms at 64px, 8.15 at 256, 25.5 at 1024). Splitting a
pass therefore pays the ~4 ms fixed cost twice to save padding that is always
cheaper than it, so the whole batch is a single pass.

The crops are prepared on the batcher's thread. Preparing them in the callers'
threads instead was measured on a real flight and gained nothing: the work is
many small calls that hold the GIL, so spreading it over the callers only made
them contend with each other and with the model pass.
"""

import logging
import queue
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.ocr.mixed import prepare_crop, recognize_prepared


logger = logging.getLogger(__name__)

# Blank margin around each crop on the CPU path's canvas, so neighbouring
# crops cannot bleed into one another's box.
_CANVAS_GAP = 4

# A request waits this long for its batch to come back before giving up. The
# pipeline applies its own stage timeout on top of this.
_REQUEST_TIMEOUT_SECONDS = 30.0

# (box, text, confidence) for one ROI
Prediction = tuple[Any, str, float]


@dataclass
class _Request:
    """One frame's ROIs, waiting to be recognised as part of a batch."""

    image: np.ndarray
    # ([x_min, x_max, y_min, y_max], allowlist) per ROI
    items: list[tuple[list[int], str | None]]
    done: threading.Event
    predictions: list[Prediction | None] = field(default_factory=list)
    error: BaseException | None = None


class BatchedRecognizer:
    """Serialises recognition onto one thread and batches across frames.

    Callers (the OCR worker threads, one per in-flight frame) hand over their
    boxes and block. A single worker thread drains everything queued, runs the
    recogniser over it -- one pass on GPU, one call per allowlist on CPU -- and
    hands each caller back its own predictions in the order it asked for them.
    """

    def __init__(
        self,
        reader: Any,
        max_batch_requests: int = 8,
        request_timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        """Initialise the batcher.

        Args:
            reader: The EasyOCR Reader whose model runs the batches.
            max_batch_requests: Most frames to merge into one pass. Beyond the
                in-flight frame count this has no effect.
            request_timeout: Seconds a caller waits for its batch.
        """
        self._reader = reader
        self._max_batch_requests = max_batch_requests
        self._request_timeout = request_timeout
        self._queue: queue.Queue[_Request | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()

    def recognize(
        self,
        image: np.ndarray,
        items: list[tuple[list[int], str | None]],
    ) -> list[Prediction | None]:
        """Recognise one frame's ROIs, batched with the other waiting frames.

        All of a frame's ROIs are handed over at once, whatever allowlists they
        need: splitting them across calls would make the frame wait for a batch
        per allowlist instead of one batch in total.

        Args:
            image: Grayscale frame the boxes refer to.
            items: ([x_min, x_max, y_min, y_max], allowlist) per ROI, where the
                allowlist is the characters the recogniser may emit for that
                box, or None for all of them.

        Returns:
            One (box, text, confidence) per item, in the order the items were
            given and with the item's own box, or None where the recogniser
            returned nothing for that box.
        """
        if not items:
            return []
        self._ensure_worker()
        request = _Request(
            image=image,
            items=items,
            done=threading.Event(),
            predictions=[None] * len(items),
        )
        self._queue.put(request)
        if not request.done.wait(self._request_timeout):
            raise TimeoutError("OCR batcher did not return within the request timeout")
        if request.error is not None:
            raise request.error
        return request.predictions

    def close(self) -> None:
        """Stop the worker thread. Further calls restart it."""
        with self._thread_lock:
            thread, self._thread = self._thread, None
        if thread is not None:
            self._queue.put(None)
            thread.join(timeout=5.0)

    def _ensure_worker(self) -> None:
        """Start the worker thread on first use."""
        if self._thread is not None:
            return
        with self._thread_lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="ocr-batcher", daemon=True
                )
                self._thread.start()

    def _run(self) -> None:
        """Drain the queue, one batch at a time, until stopped."""
        while True:
            first = self._queue.get()
            if first is None:
                return
            batch = [first]
            stopping = False
            while len(batch) < self._max_batch_requests:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    stopping = True
                    break
                batch.append(nxt)
            try:
                self._run_batch(batch)
            except BaseException as exc:  # noqa: BLE001 - reported to every caller
                logger.error("OCR batch failed: %s", exc, exc_info=exc)
                for request in batch:
                    request.error = exc
            finally:
                for request in batch:
                    request.done.set()
            if stopping:
                return

    def _run_batch(self, batch: list[_Request]) -> None:
        """Recognise every box in the batch.

        On GPU the whole batch is one pass of the model, each box under its own
        allowlist (see ocr.mixed). On CPU EasyOCR recognises box by box anyway,
        so the boxes go to ``recognize`` grouped by allowlist, as before.
        """
        members = [(request, slot) for request in batch for slot in range(len(request.items))]
        if self._on_gpu:
            self._recognize_prepared(members)
            return

        groups: dict[str | None, list[tuple[_Request, int]]] = defaultdict(list)
        for request, slot in members:
            groups[request.items[slot][1]].append((request, slot))
        for group in groups.values():
            self._recognize_group(group)

    @property
    def _on_gpu(self) -> bool:
        return getattr(self._reader, "device", "cpu") != "cpu"

    def _recognize_prepared(self, members: list[tuple[_Request, int]]) -> None:
        """Prepare every crop and run the model once over all of them (GPU)."""
        ready = []
        for request, slot in members:
            box, allowlist = request.items[slot]
            crop = prepare_crop(request.image, box)
            if crop is not None:  # a degenerate crop keeps its None
                ready.append((request, slot, allowlist, crop))
        readings = recognize_prepared(
            self._reader,
            [crop for _, _, _, crop in ready],
            [allowlist for _, _, allowlist, _ in ready],
        )
        for (request, slot, _, _), (text, confidence) in zip(ready, readings):
            request.predictions[slot] = (request.items[slot][0], text, confidence)

    def _recognize_group(self, members: list[tuple[_Request, int]]) -> None:
        """Paste crops sharing one allowlist onto a canvas and recognise them (CPU)."""
        canvas, placed = self._build_canvas(members)
        if not placed:
            return
        canvas_boxes = [box for _, _, box in placed]
        first_request, first_slot, _ = placed[0]
        predictions = self._reader.recognize(
            canvas,
            horizontal_list=canvas_boxes,
            free_list=[],
            allowlist=first_request.items[first_slot][1],
            batch_size=len(canvas_boxes),
            reformat=False,
        )
        # EasyOCR may reorder boxes, so match each prediction back to the crop
        # it came from by that crop's top-left corner on the canvas.
        by_corner = {
            (int(box[0][0]), int(box[0][1])): (text, confidence)
            for box, text, confidence in predictions
        }
        for request, slot, canvas_box in placed:
            prediction = by_corner.get((canvas_box[0], canvas_box[2]))
            if prediction is not None:
                request.predictions[slot] = (request.items[slot][0], *prediction)

    @staticmethod
    def _build_canvas(
        members: list[tuple[_Request, int]],
    ) -> tuple[np.ndarray, list[tuple[_Request, int, list[int]]]]:
        """Stack every member's ROI crop into one image, one crop per row.

        Returns the canvas and, for each crop actually placed on it, the
        request and slot it belongs to plus its [x_min, x_max, y_min, y_max]
        box on the canvas. Empty crops are left out, so a request whose ROI is
        degenerate simply keeps its None prediction.
        """
        crops: list[tuple[_Request, int, np.ndarray]] = []
        for request, slot in members:
            x_min, x_max, y_min, y_max = request.items[slot][0]
            crop = request.image[y_min:y_max, x_min:x_max]
            if crop.size:
                crops.append((request, slot, crop))

        if not crops:
            return np.zeros((1, 1), dtype=np.uint8), []

        width = max(crop.shape[1] for _, _, crop in crops) + 2 * _CANVAS_GAP
        height = sum(crop.shape[0] + _CANVAS_GAP for _, _, crop in crops) + _CANVAS_GAP
        canvas = np.zeros((height, width), dtype=crops[0][2].dtype)

        placed: list[tuple[_Request, int, list[int]]] = []
        y = _CANVAS_GAP
        for request, slot, crop in crops:
            h, w = crop.shape[:2]
            canvas[y : y + h, _CANVAS_GAP : _CANVAS_GAP + w] = crop
            placed.append((request, slot, [_CANVAS_GAP, _CANVAS_GAP + w, y, y + h]))
            y += h + _CANVAS_GAP
        return canvas, placed
