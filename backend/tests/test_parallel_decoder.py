"""Tests for parallel decoding of seekable sources.

These run real OpenCV/FFmpeg decoding over a small generated video, because
what matters is that seeking lands exactly where serial decoding would: every
frame must come back pixel-identical, under the same frame number.
"""

import asyncio
import hashlib
import threading

import cv2
import numpy as np
import pytest

from src.capture import FrameExtractor, FrameExtractorConfig
from src.capture.parallel_decoder import ParallelFileDecoder
from src.capture.planning import parallel_plan
from src.core.enums import PipelineStatus


N_FRAMES = 600


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> str:
    """A 600-frame clip whose frames all differ, so a wrong frame shows."""
    path = tmp_path_factory.mktemp("video") / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 240))
    assert writer.isOpened()
    rng = np.random.default_rng(0)
    for i in range(N_FRAMES):
        frame = rng.integers(0, 60, (240, 320, 3), dtype=np.uint8)
        cv2.putText(frame, str(i), (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 5)
        writer.write(frame)
    writer.release()
    return str(path)


def digest(frame: np.ndarray) -> str:
    return hashlib.md5(frame.tobytes()).hexdigest()


def serial_frames(path: str, first: int, skip: int) -> list[tuple[int, str]]:
    """What the serial extractor keeps: frames first, first+skip, ..."""
    cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
    out = []
    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if n >= first and (n - first) % skip == 0:
            out.append((n, digest(frame)))
        n += 1
    cap.release()
    return out


def parallel_frames(decoder: ParallelFileDecoder) -> list[tuple[int, str]]:
    out = []
    while (item := decoder.next_frame()) is not None:
        out.append((item[0], digest(item[1])))
    return out


def make_decoder(path, first, skip, workers=4, chunk=3, max_buffered=None):
    return ParallelFileDecoder(
        path,
        first_frame=first,
        skip=skip,
        workers=workers,
        chunk_frames=chunk,
        max_buffered=max_buffered or (workers + 1) * chunk,
        threads_per_decoder=2,
        reported_frame_count=N_FRAMES,
    )


@pytest.mark.parametrize("first,skip,chunk", [(29, 30, 2), (16, 17, 3), (44, 45, 1), (0, 7, 5)])
def test_frames_match_serial_decoding_exactly(video, first, skip, chunk):
    decoder = make_decoder(video, first, skip, chunk=chunk)
    try:
        got = parallel_frames(decoder)
    finally:
        decoder.close()

    assert got == serial_frames(video, first, skip)


def test_end_of_source_is_reported_once_and_stays(video):
    decoder = make_decoder(video, 29, 30)
    try:
        frames = parallel_frames(decoder)
        assert frames[-1][0] == 599  # the last frame kept at skip 30
        assert decoder.next_frame() is None
    finally:
        decoder.close()


def test_start_past_the_end_yields_nothing(video):
    decoder = make_decoder(video, N_FRAMES + 50, 30)
    try:
        assert decoder.next_frame() is None
    finally:
        decoder.close()


def test_buffer_stays_within_its_bound(video):
    decoder = make_decoder(video, 9, 10, workers=4, chunk=2, max_buffered=6)
    try:
        for _ in range(20):
            assert decoder.next_frame() is not None
            # let the workers race ahead while the consumer is slow
            threading.Event().wait(0.02)
    finally:
        decoder.close()

    assert decoder.peak_buffered <= 6


def test_worker_failure_ends_the_run_at_its_chunk(video, monkeypatch):
    # A worker that fails mid-chunk ends the run where its chunk starts;
    # everything before it, decoded by the other workers, still comes out.
    real = ParallelFileDecoder._decode_chunk

    def failing(self, capture, start):
        if start == 6:
            raise RuntimeError("decoder blew up")
        return real(self, capture, start)

    monkeypatch.setattr(ParallelFileDecoder, "_decode_chunk", failing)
    decoder = make_decoder(video, 29, 30, workers=4, chunk=2)
    try:
        got = parallel_frames(decoder)
    finally:
        decoder.close()

    assert got == serial_frames(video, 29, 30)[:6]


def test_close_stops_the_workers(video):
    decoder = make_decoder(video, 0, 2, workers=4, chunk=4)
    assert decoder.next_frame() is not None
    decoder.close()

    assert decoder.next_frame() is None
    assert not any(t.is_alive() for t in decoder._threads)


# --- through FrameExtractor ---


async def run_extractor(path: str, skip: int, workers: int, change_skip_after=None):
    """Run the extractor over the clip until it reaches the end."""
    extractor = FrameExtractor()
    seen: list[tuple[int, int, str]] = []
    done = asyncio.Event()
    used_parallel = []

    async def on_frame(frame, seq, frame_number, fps):
        seen.append((seq, frame_number, digest(frame)))
        used_parallel.append(extractor._decoder is not None)
        if change_skip_after is not None and seq == change_skip_after[0]:
            extractor._config.skip_frames = change_skip_after[1]

    async def on_status(status):
        if status == PipelineStatus.RECONNECTING:
            done.set()

    extractor.on_frame(on_frame)
    extractor.on_status_change(on_status)
    extractor.start(
        FrameExtractorConfig(source_url=path, skip_frames=skip, decode_workers=workers)
    )
    await asyncio.wait_for(done.wait(), timeout=60)
    extractor.stop()
    await asyncio.sleep(0)
    return seen, used_parallel


def test_extractor_decodes_in_parallel_and_matches_serial(video):
    serial, used_serial = asyncio.run(run_extractor(video, 30, workers=1))
    parallel, used_parallel = asyncio.run(run_extractor(video, 30, workers=4))

    assert not any(used_serial)
    assert all(used_parallel)
    assert parallel == serial
    assert [n for _, n, _ in parallel] == list(range(29, N_FRAMES, 30))


def test_short_skip_stays_serial(video):
    # Decoding is not the bottleneck at short intervals, and the chunks would
    # not span enough keyframes to pay for their seeks.
    seen, used = asyncio.run(run_extractor(video, 5, workers=4))

    assert not any(used)
    assert [n for _, n, _ in seen] == list(range(4, N_FRAMES, 5))


def test_skip_change_mid_run_hands_over_to_serial(video):
    # Five frames at skip 30 in parallel, then skip 5 serially from there on.
    seen, used = asyncio.run(run_extractor(video, 30, workers=4, change_skip_after=(5, 5)))

    numbers = [n for _, n, _ in seen]
    assert numbers == sorted(set(numbers))  # in order, nothing repeated
    assert used[0] and not used[-1]
    # every frame is the one serial decoding gives for that number, after
    # the extractor's resize to 1080p
    cap = cv2.VideoCapture(video, cv2.CAP_FFMPEG)
    reference = {}
    while (read := cap.read())[0]:
        reference[len(reference)] = digest(
            cv2.resize(read[1], (1920, 1080), interpolation=cv2.INTER_LINEAR)
        )
    cap.release()
    assert all(reference[n] == d for _, n, d in seen)
    tail = numbers[-10:]
    assert all(b - a == 5 for a, b in zip(tail, tail[1:]))


@pytest.mark.parametrize(
    "skip,expected_workers",
    [(30, 8), (24, 8), (15, 5), (8, 2), (5, None), (1, None)],
)
def test_plan_trades_decoders_for_chunk_length(skip, expected_workers, monkeypatch):
    # Every chunk must span at least 120 source frames to pay for its seek,
    # and all decoders' chunks must fit the 48-frame buffer, so shorter skip
    # intervals get fewer decoders, and the shortest none at all. Enough cores
    # that the physical-core cap doesn't bind.
    monkeypatch.setattr("src.capture.planning.os.cpu_count", lambda: 16)
    extractor = FrameExtractor()
    extractor._config = FrameExtractorConfig(source_url="clip.mp4", skip_frames=skip)

    plan = parallel_plan(extractor._config.decode_workers, skip)

    if expected_workers is None:
        assert plan is None
        return
    chunk, workers, threads = plan
    assert workers == expected_workers
    assert chunk * skip >= 120
    assert (workers + 1) * chunk <= 48
    assert threads >= 1


@pytest.mark.parametrize("cpus,expected", [(16, 8), (8, 4), (4, 2), (2, None), (1, None)])
def test_plan_never_runs_more_decoders_than_physical_cores(cpus, expected, monkeypatch):
    monkeypatch.setattr("src.capture.planning.os.cpu_count", lambda: cpus)
    extractor = FrameExtractor()
    extractor._config = FrameExtractorConfig(source_url="clip.mp4", skip_frames=30)

    plan = parallel_plan(extractor._config.decode_workers, 30)

    assert (plan[1] if plan else None) == expected


def test_plan_respects_a_serial_setting():
    extractor = FrameExtractor()
    extractor._config = FrameExtractorConfig(source_url="clip.mp4", decode_workers=1)

    assert parallel_plan(extractor._config.decode_workers, 30) is None
