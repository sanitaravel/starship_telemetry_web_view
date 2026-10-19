"""Tests for BatchedRecognizer: cross-frame batching of recogniser passes."""

import threading
import time

import numpy as np
import pytest

from src.ocr_batcher import BatchedRecognizer


class FakeReader:
    """Reads back the single pixel value each canvas box was filled with.

    Every crop in these tests is a constant block, so the value identifies
    which caller's box a prediction belongs to. Results come back in reverse,
    as EasyOCR is free to reorder them.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def recognize(self, image, horizontal_list, free_list, allowlist, batch_size, reformat):
        self.calls.append(
            {
                "boxes": list(horizontal_list),
                "allowlist": allowlist,
                "batch_size": batch_size,
                "shape": image.shape,
            }
        )
        predictions = []
        for x_min, x_max, y_min, y_max in horizontal_list:
            value = int(image[y_min, x_min])
            predictions.append(([[x_min, y_min]], str(value), 0.9))
        return list(reversed(predictions))


def frame_with(value: int, width: int = 120, height: int = 40) -> np.ndarray:
    """A grayscale frame holding one constant block at a known box."""
    img = np.zeros((200, 400), dtype=np.uint8)
    img[20 : 20 + height, 30 : 30 + width] = value
    return img


BOX = [30, 150, 20, 60]


def test_single_request_round_trips():
    reader = FakeReader()
    batcher = BatchedRecognizer(reader)

    predictions = batcher.recognize(frame_with(77), [(BOX, None)])

    assert [p[1] for p in predictions] == ["77"]
    batcher.close()


class HoldingReader(FakeReader):
    """Holds its first pass until the other callers are queued behind it."""

    def __init__(self, batcher_ref: list, waiting: int) -> None:
        super().__init__()
        self._batcher_ref = batcher_ref
        self._waiting = waiting

    def recognize(self, *args, **kwargs):
        if not self.calls:
            queue = self._batcher_ref[0]._queue
            deadline = time.monotonic() + 5
            while queue.qsize() < self._waiting and time.monotonic() < deadline:
                time.sleep(0.005)
        return super().recognize(*args, **kwargs)


def test_concurrent_requests_share_one_recognizer_pass():
    values = [11, 22, 33, 44, 55, 66]
    ref: list = []
    reader = HoldingReader(ref, waiting=len(values) - 1)
    batcher = BatchedRecognizer(reader)
    ref.append(batcher)
    results: dict[int, list] = {}

    def call(value: int) -> None:
        results[value] = batcher.recognize(frame_with(value), [(BOX, None)])

    threads = [threading.Thread(target=call, args=(v,)) for v in values]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Every caller gets its own frame's value back, whatever the batching
    for value in values:
        assert [p[1] for p in results[value]] == [str(value)]
    # the first caller's pass, then one pass for the five queued behind it
    assert len(reader.calls) == 2
    batcher.close()


def test_boxes_are_grouped_by_allowlist():
    reader = FakeReader()
    batcher = BatchedRecognizer(reader)
    results = {}
    ready = threading.Barrier(2)

    def call(value, allowlist):
        ready.wait()
        results[allowlist] = batcher.recognize(frame_with(value), [(BOX, allowlist)])

    threads = [
        threading.Thread(target=call, args=(11, "0123456789")),
        threading.Thread(target=call, args=(22, "ABC")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert {call["allowlist"] for call in reader.calls} == {"0123456789", "ABC"}
    assert all(len(call["boxes"]) == 1 for call in reader.calls)
    assert [p[1] for p in results["0123456789"]] == ["11"]
    assert [p[1] for p in results["ABC"]] == ["22"]
    batcher.close()


def test_boxes_of_different_widths_share_one_pass():
    # Splitting on width costs the recogniser's fixed per-pass cost twice,
    # which is dearer than the padding it saves (see ocr_batcher).
    reader = FakeReader()
    batcher = BatchedRecognizer(reader)
    img = np.zeros((200, 400), dtype=np.uint8)
    img[20:60, 30:70] = 11  # narrow
    img[100:140, 30:390] = 22  # nine times wider

    predictions = batcher.recognize(
        img, [([30, 70, 20, 60], None), ([30, 390, 100, 140], None)]
    )

    assert len(reader.calls) == 1
    assert [p[1] for p in predictions] == ["11", "22"]
    batcher.close()


def test_multiple_boxes_keep_their_order():
    reader = FakeReader()
    batcher = BatchedRecognizer(reader)
    img = np.zeros((200, 400), dtype=np.uint8)
    img[20:60, 30:150] = 11
    img[70:110, 30:150] = 22
    img[120:160, 30:150] = 33

    predictions = batcher.recognize(
        img,
        [([30, 150, 20, 60], None), ([30, 150, 70, 110], None),
         ([30, 150, 120, 160], None)],
    )

    assert [p[1] for p in predictions] == ["11", "22", "33"]
    batcher.close()


def test_empty_box_yields_no_prediction():
    reader = FakeReader()
    batcher = BatchedRecognizer(reader)
    img = np.zeros((200, 400), dtype=np.uint8)

    predictions = batcher.recognize(img, [([30, 30, 20, 60], None)])

    assert predictions == [None]
    batcher.close()


def test_no_boxes_short_circuits():
    reader = FakeReader()
    batcher = BatchedRecognizer(reader)

    assert batcher.recognize(frame_with(1), []) == []
    assert reader.calls == []


def test_recognizer_failure_reaches_the_caller():
    class Failing(FakeReader):
        def recognize(self, *a, **k):
            raise RuntimeError("model exploded")

    batcher = BatchedRecognizer(Failing())

    with pytest.raises(RuntimeError, match="model exploded"):
        batcher.recognize(frame_with(5), [(BOX, None)])
    batcher.close()


def test_worker_restarts_after_close():
    reader = FakeReader()
    batcher = BatchedRecognizer(reader)

    batcher.recognize(frame_with(7), [(BOX, None)])
    batcher.close()
    predictions = batcher.recognize(frame_with(8), [(BOX, None)])

    assert [p[1] for p in predictions] == ["8"]
    batcher.close()


# --- GPU: crops prepared by the caller, the whole batch in one model pass ---


class FakeGpuReader(FakeReader):
    device = "cuda"


def fake_gpu_path(monkeypatch):
    """Substitute ocr_mixed's two halves, recording which thread ran each."""
    log = {"prepare_threads": [], "passes": []}

    def prepare(image, box):
        log["prepare_threads"].append(threading.current_thread().name)
        x_min, _, y_min, _ = box
        return int(image[y_min, x_min])  # stands in for a PreparedCrop

    def recognize(reader, crops, allowlists):
        log["passes"].append((threading.current_thread().name, list(allowlists)))
        return [(f"{allowlist}:{crop}", 0.9) for crop, allowlist in zip(crops, allowlists)]

    monkeypatch.setattr("src.ocr_batcher.prepare_crop", prepare)
    monkeypatch.setattr("src.ocr_batcher.recognize_prepared", recognize)
    return log


def three_rows():
    img = np.zeros((200, 400), dtype=np.uint8)
    img[20:60, 30:150] = 11
    img[70:110, 30:150] = 22
    img[120:160, 30:150] = 33
    return img, [
        ([30, 150, 20, 60], "0123456789:_"),
        ([30, 150, 70, 110], "0123456789."),
        ([30, 150, 120, 160], None),
    ]


def test_gpu_batch_is_one_pass_with_mixed_allowlists(monkeypatch):
    log = fake_gpu_path(monkeypatch)
    reader = FakeGpuReader()
    batcher = BatchedRecognizer(reader)
    img, items = three_rows()

    predictions = batcher.recognize(img, items)

    assert [allowlists for _, allowlists in log["passes"]] == [
        ["0123456789:_", "0123456789.", None]
    ]
    assert reader.calls == []  # recognize() not used on GPU
    assert [p[1] for p in predictions] == ["0123456789:_:11", "0123456789.:22", "None:33"]
    # each prediction carries the frame box it belongs to
    assert [p[0] for p in predictions] == [box for box, _ in items]
    batcher.close()


def test_gpu_preparation_and_pass_run_on_the_batcher_thread(monkeypatch):
    log = fake_gpu_path(monkeypatch)
    batcher = BatchedRecognizer(FakeGpuReader())
    img, items = three_rows()
    results = {}

    def caller():
        results["out"] = batcher.recognize(img, items)

    thread = threading.Thread(target=caller, name="ocr_0")
    thread.start()
    thread.join(timeout=10)

    # Preparing in the callers' threads gained nothing (GIL-bound), so the
    # callers only queue their boxes
    assert log["prepare_threads"] == ["ocr-batcher"] * 3
    assert [thread_name for thread_name, _ in log["passes"]] == ["ocr-batcher"]
    batcher.close()


def test_gpu_degenerate_crop_keeps_its_none(monkeypatch):
    log = fake_gpu_path(monkeypatch)
    monkeypatch.setattr(
        "src.ocr_batcher.prepare_crop",
        lambda image, box: None if box[0] == box[1] else int(image[box[2], box[0]]),
    )
    batcher = BatchedRecognizer(FakeGpuReader())
    img, items = three_rows()
    items[1] = ([30, 30, 70, 110], "0123456789.")  # zero width

    predictions = batcher.recognize(img, items)

    assert predictions[1] is None
    assert [allowlists for _, allowlists in log["passes"]] == [["0123456789:_", None]]
    batcher.close()


def test_cpu_keeps_one_recognize_per_allowlist(monkeypatch):
    log = fake_gpu_path(monkeypatch)
    reader = FakeReader()
    batcher = BatchedRecognizer(reader)
    img, items = three_rows()

    batcher.recognize(img, items[:2])

    assert log["prepare_threads"] == [] and log["passes"] == []
    assert sorted(c["allowlist"] for c in reader.calls) == ["0123456789.", "0123456789:_"]
    batcher.close()
