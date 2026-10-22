"""Behaviour of FrameExtractor's capture loop, pinned across the split of
``_capture_loop`` into ``_open_source`` / ``_read_frames`` / ``_read_next`` /
``_recover_source`` / ``_dispatch_frame``.

The expectations are computed from the semantics of the pre-split loop
(``src/frame_extractor.py`` at 53aaf18), not from the current code:

- the frame counter counts every frame read or grabbed since the source was
  (re)opened and starts at 0 on start and after every reconnect;
- a frame is kept when the counter, after counting it, is a multiple of the
  skip interval in force; its ``frame_number`` is the counter minus one, and
  kept frames get sequence numbers 1, 2, 3, ... across reconnects;
- a reconnect reads one frame from the reopened source to check it, so after
  a reconnect ``frame_number`` lags the reopened source's position by one;
- switching from parallel to serial decoding seeks the main capture to the
  counter once; a fresh run never seeks.

Sources are stubbed through ``src.capture.source.cv2.VideoCapture``. Frames
are tiny and already at the target size, so the loop hands the source's own
frame objects to the callbacks and each one shows which session and source
position it came from.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.capture import FrameExtractor, FrameExtractorConfig, SourceUnavailable
from src.enums import PipelineStatus

# Where the extractor constructs its OpenCV captures and parallel decoders
VIDEO_CAPTURE = "src.capture.source.cv2.VideoCapture"
PARALLEL_DECODER = "src.capture.planning.ParallelFileDecoder"

WIDTH, HEIGHT = 8, 6
URL = "http://test.example.com/stream"


def make_frame(session: int, position: int) -> np.ndarray:
    """A frame that records the session it was read in and its position."""
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[..., 0] = session
    frame[..., 1] = position % 256
    frame[..., 2] = position // 256
    return frame


def origin(frame: np.ndarray) -> tuple[int, int]:
    """(session, source position) of a frame from make_frame."""
    return int(frame[0, 0, 0]), int(frame[0, 0, 1]) + 256 * int(frame[0, 0, 2])


class FakeCapture:
    """A cv2.VideoCapture over ``length`` frames; closed when length is None."""

    def __init__(self, session: int, length: int | None, on_grab=None) -> None:
        self.session = session
        self.length = length
        self.on_grab = on_grab
        self.pos = 0
        self.seeks: list[int] = []
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 - OpenCV's name
        return self.length is not None

    def get(self, prop: int) -> float:
        return {
            cv2.CAP_PROP_FRAME_WIDTH: WIDTH,
            cv2.CAP_PROP_FRAME_HEIGHT: HEIGHT,
            cv2.CAP_PROP_FPS: 30.0,
        }.get(prop, 0.0)

    def set(self, prop: int, value: float) -> bool:
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self.seeks.append(int(value))
            self.pos = int(value)
        return True

    def getBackendName(self) -> str:  # noqa: N802 - OpenCV's name
        return "FAKE"

    def _available(self) -> bool:
        return self.length is not None and not self.released and self.pos < self.length

    def grab(self) -> bool:
        if not self._available():
            return False
        if self.on_grab is not None:
            self.on_grab(self.pos)
        self.pos += 1
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self._available():
            return False, None
        frame = make_frame(self.session, self.pos)
        self.pos += 1
        return True, frame

    def release(self) -> None:
        self.released = True


class FakeSource:
    """Stands in for cv2.VideoCapture: each call opens the next session.

    Once the scripted sessions are used up, every further open fails, and
    ``on_exhausted`` (if given) runs.
    """

    def __init__(self, lengths: list[int], on_exhausted=None, on_grab=None) -> None:
        self.lengths = list(lengths)
        self.on_exhausted = on_exhausted
        self.on_grab = on_grab
        self.opened: list[FakeCapture] = []

    def __call__(self, url: str, *args) -> FakeCapture:
        index = len(self.opened)
        if index < len(self.lengths):
            capture = FakeCapture(index, self.lengths[index], self.on_grab)
        else:
            capture = FakeCapture(index, None)
            if self.on_exhausted is not None:
                self.on_exhausted()
        self.opened.append(capture)
        return capture


def old_loop_expectation(lengths: list[int], skip: int) -> list[tuple[int, int, int, int]]:
    """What the pre-split serial loop delivers over these sessions.

    Returns (seq, frame_number, session, source position) per kept frame.
    Session 0 is the initial open; each later one is a successful reconnect,
    whose check read consumes the session's frame 0 before the counter is
    reset to 0.
    """
    kept = []
    seq = 0
    for session, length in enumerate(lengths):
        position = 0 if session == 0 else 1
        counter = 0
        while position < length:
            counter += 1
            if counter % skip == 0:
                seq += 1
                kept.append((seq, counter - 1, session, position))
            position += 1
    return kept


def make_extractor(skip: int, decode_workers: int = 1) -> FrameExtractor:
    extractor = FrameExtractor()
    # Reconnect at once instead of after 1 s, 2 s, ...
    extractor._INITIAL_BACKOFF_S = 0.001
    extractor._MAX_BACKOFF_S = 0.001
    extractor._config = FrameExtractorConfig(
        source_url=URL,
        skip_frames=skip,
        target_width=WIDTH,
        target_height=HEIGHT,
        decode_workers=decode_workers,
    )
    extractor._stop_event.clear()
    return extractor


def run_on_private_loop(coro):
    """Run ``coro`` on a loop of its own, leaving the thread's loop alone.

    asyncio.run() would set the thread's event loop to None when done, and
    later sync tests that call asyncio.ensure_future (FrameExtractor.start)
    would then fail with "There is no current event loop".
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


class Recorder:
    """Collects frame and status callbacks."""

    def __init__(self, extractor: FrameExtractor) -> None:
        self.frames: list[tuple[int, int, int, int]] = []
        self.statuses: list[PipelineStatus] = []
        extractor.on_frame(self.on_frame)
        extractor.on_status_change(self.on_status)

    async def on_frame(self, frame, seq, frame_number, fps) -> None:
        self.frames.append((seq, frame_number, *origin(frame)))

    async def on_status(self, status: PipelineStatus) -> None:
        self.statuses.append(status)


async def run_until_sources_run_out(
    lengths: list[int], skip: int
) -> tuple[Recorder, FakeSource, FrameExtractor]:
    """Run the real loop, reconnects included, over scripted sessions.

    When every session has been read to its end, the next reopen fails and
    sets the stop event, as a stop() that has not cancelled the task yet
    would; the reconnect then gives up and the loop exits.
    """
    extractor = make_extractor(skip)
    recorder = Recorder(extractor)
    source = FakeSource(lengths, on_exhausted=extractor._stop_event.set)
    with patch(VIDEO_CAPTURE, source):
        await asyncio.wait_for(extractor._capture_loop(), timeout=10)
    return recorder, source, extractor


class TestReconnectRestartsNumbering:
    """After a failed read and a successful reconnect, the counter and the
    skip filter start over exactly as the pre-split loop did."""

    async def test_numbering_restarts_from_zero_after_reconnect(self) -> None:
        recorder, source, _ = await run_until_sources_run_out([10, 9], skip=3)

        # Session 0: counter 3, 6, 9 -> frames 2, 5, 8. Session 1: the check
        # read takes position 0, then counter 3, 6 land on positions 3, 6.
        assert recorder.frames == [
            (1, 2, 0, 2),
            (2, 5, 0, 5),
            (3, 8, 0, 8),
            (4, 2, 1, 3),
            (5, 5, 1, 6),
        ]
        assert recorder.frames == old_loop_expectation([10, 9], 3)
        # One initial open, one successful reopen, one failed reopen
        assert len(source.opened) == 3

    async def test_status_sequence_through_reconnect_and_give_up(self) -> None:
        recorder, source, extractor = await run_until_sources_run_out([4, 4], skip=2)

        assert recorder.statuses == [
            PipelineStatus.RUNNING,
            PipelineStatus.RECONNECTING,
            PipelineStatus.RUNNING,
            PipelineStatus.RECONNECTING,
            PipelineStatus.DISCONNECTED,
            PipelineStatus.STOPPED,
        ]
        assert extractor.status == PipelineStatus.STOPPED
        assert extractor._capture is None
        assert all(c.released for c in source.opened)

    async def test_reconnect_after_partial_grab_batch_resets_counter(self) -> None:
        # Session 0 ends in the middle of a grab batch (positions 5..6 of a
        # 3-frame batch exist, 7 does not); the frames grabbed before the
        # failure must not carry over into session 1's numbering.
        recorder, _, _ = await run_until_sources_run_out([7, 8], skip=4)

        assert recorder.frames == old_loop_expectation([7, 8], 4)
        assert [(n, s) for _, n, s, _ in recorder.frames] == [(3, 0), (3, 1)]

    async def test_reconnect_resyncs_after_parallel_decoding(self) -> None:
        # A parallel decoder that runs dry also goes through reconnect; the
        # reopened capture is read from where it is, never seeked.
        extractor = make_extractor(skip=10, decode_workers=4)
        recorder = Recorder(extractor)
        source = FakeSource([30, 12], on_exhausted=extractor._stop_event.set)
        decoder = ScriptedDecoder([(9, make_frame(0, 9))])

        with (
            patch(VIDEO_CAPTURE, source),
            patch(PARALLEL_DECODER, decoder.construct),
            patch.object(extractor, "_probe_seekable", return_value=1000),
            patch.object(
                extractor, "_parallel_plan",
                side_effect=lambda skip: (2, 2, 1) if skip >= 10 and not decoder.started else None,
            ),
        ):
            await asyncio.wait_for(extractor._capture_loop(), timeout=10)

        assert recorder.frames == [(1, 9, 0, 9), (2, 9, 1, 10)]
        assert decoder.closed
        assert source.opened[1].seeks == []


class TestSkipFilterMatchesOldLoop:
    """Which frames are kept, and the numbers they get, with skip_frames=N."""

    @pytest.mark.parametrize("skip", [1, 2, 3, 5, 7])
    async def test_single_session(self, skip: int) -> None:
        recorder, _, _ = await run_until_sources_run_out([23], skip=skip)

        expected = old_loop_expectation([23], skip)
        assert recorder.frames == expected
        # On the first session, frame_number is the source position itself
        assert [n for _, n, _, _ in recorder.frames] == list(range(skip - 1, 23, skip))

    @given(
        skip=st.integers(min_value=1, max_value=7),
        first=st.integers(min_value=0, max_value=25),
        later=st.lists(st.integers(min_value=1, max_value=25), max_size=2),
    )
    @settings(max_examples=40, deadline=None)
    def test_property_matches_old_semantics(
        self, skip: int, first: int, later: list[int]
    ) -> None:
        """For any sessions and skip, kept frames, frame numbers, sequence
        numbers and the frames' origins match the pre-split loop."""
        lengths = [first, *later]
        recorder, _, _ = run_on_private_loop(run_until_sources_run_out(lengths, skip))

        assert recorder.frames == old_loop_expectation(lengths, skip)

    async def test_skip_change_mid_run_takes_effect_at_the_next_batch(self) -> None:
        # Pre-split: skip 3 keeps frame 2; the callback switches to skip 2,
        # so the next batch reads counter 4 straight away (4 % 2 == 0), then
        # every second frame.
        extractor = make_extractor(skip=3)
        seen: list[int] = []

        async def on_frame(frame, seq, frame_number, fps) -> None:
            seen.append(frame_number)
            assert origin(frame) == (0, frame_number)
            if seq == 1:
                extractor._config.skip_frames = 2

        extractor.on_frame(on_frame)
        source = FakeSource([12], on_exhausted=extractor._stop_event.set)
        with patch(VIDEO_CAPTURE, source):
            await asyncio.wait_for(extractor._capture_loop(), timeout=10)

        assert seen == [2, 3, 5, 7, 9, 11]


    async def test_skip_change_during_a_grab_batch_is_filtered_by_the_new_skip(
        self,
    ) -> None:
        # Pre-split: the batch is planned with the skip in force when it
        # starts (skip 4: grab 0-2, read 3), but the kept-frame check uses
        # skip_frames as it is when the read returns. Changed to 3 during
        # the batch, counter 4 is dropped (4 % 3 != 0) and the next batch
        # lands on counter 6, then every third frame.
        extractor = make_extractor(skip=4)
        recorder = Recorder(extractor)
        config = extractor._config
        assert config is not None

        def change_skip(position: int) -> None:
            if position == 1:
                config.skip_frames = 3

        source = FakeSource([12], on_exhausted=extractor._stop_event.set, on_grab=change_skip)
        with patch(VIDEO_CAPTURE, source):
            await asyncio.wait_for(extractor._capture_loop(), timeout=10)

        assert recorder.frames == [(1, 5, 0, 5), (2, 8, 0, 8), (3, 11, 0, 11)]


class ScriptedDecoder:
    """Stands in for ParallelFileDecoder, handing out scripted frames."""

    def __init__(self, frames: list[tuple[int, np.ndarray]]) -> None:
        self.frames = list(frames)
        self.started = False
        self.closed = False
        self.kwargs: dict = {}
        self._skip = 0

    def construct(self, url: str, **kwargs) -> "ScriptedDecoder":
        self.started = True
        self.kwargs = kwargs
        self._skip = kwargs["skip"]
        return self

    @property
    def skip(self) -> int:
        return self._skip

    def next_frame(self):
        return self.frames.pop(0) if self.frames else None

    def close(self) -> None:
        self.closed = True


class TestParallelToSerialHandover:
    """Parallel vs serial selection, and the serial path's seek back."""

    async def test_serial_resumes_at_the_counter_after_parallel(self) -> None:
        # Pre-split: frames 9 and 19 come from the decoder at skip 10; the
        # callback then drops to skip 3, which has no parallel plan, so the
        # main capture seeks to frame 20 once and serial reading keeps
        # counter 21, 24, 27 -> frames 20, 23, 26.
        extractor = make_extractor(skip=10, decode_workers=4)
        seen: list[tuple[int, int, int, int]] = []

        async def on_frame(frame, seq, frame_number, fps) -> None:
            seen.append((seq, frame_number, *origin(frame)))
            if seq == 2:
                extractor._config.skip_frames = 3

        extractor.on_frame(on_frame)
        source = FakeSource([28], on_exhausted=extractor._stop_event.set)
        decoder = ScriptedDecoder(
            [(9, make_frame(0, 9)), (19, make_frame(0, 19)), (29, make_frame(0, 29))]
        )

        with (
            patch(VIDEO_CAPTURE, source),
            patch(PARALLEL_DECODER, decoder.construct),
            patch.object(extractor, "_probe_seekable", return_value=1000),
            patch.object(
                extractor, "_parallel_plan",
                side_effect=lambda skip: (2, 2, 1) if skip >= 10 else None,
            ),
        ):
            await asyncio.wait_for(extractor._capture_loop(), timeout=10)

        assert seen == [
            (1, 9, 0, 9),
            (2, 19, 0, 19),
            (3, 20, 0, 20),
            (4, 23, 0, 23),
            (5, 26, 0, 26),
        ]
        assert decoder.kwargs["first_frame"] == 9
        assert decoder.kwargs["skip"] == 10
        assert decoder.closed
        # One seek, on the handover, to the counter
        assert source.opened[0].seeks == [20]

    async def test_fresh_run_never_seeks_even_if_left_out_of_sync(self) -> None:
        # A previous run that ended while decoding in parallel must not make
        # the next run seek its new capture.
        extractor = make_extractor(skip=2)
        extractor._capture_in_sync = False
        extractor._frame_counter = 57
        recorder = Recorder(extractor)
        source = FakeSource([6], on_exhausted=extractor._stop_event.set)

        with patch(VIDEO_CAPTURE, source):
            await asyncio.wait_for(extractor._capture_loop(), timeout=10)

        assert source.opened[0].seeks == []
        assert [n for _, n, _, _ in recorder.frames] == [1, 3, 5]

    async def test_no_parallel_plan_never_probes_the_source(self) -> None:
        extractor = make_extractor(skip=3, decode_workers=1)
        source = FakeSource([6], on_exhausted=extractor._stop_event.set)
        with (
            patch(VIDEO_CAPTURE, source),
            patch.object(extractor, "_probe_seekable", return_value=1000) as probe,
        ):
            await asyncio.wait_for(extractor._capture_loop(), timeout=10)

        probe.assert_not_called()
        assert extractor._decoder is None


class TestEndOfStream:
    """A stream that ends and cannot be reconnected."""

    async def test_end_then_failed_reconnect_disconnects_and_exits(self) -> None:
        extractor = make_extractor(skip=2)
        recorder = Recorder(extractor)
        source = FakeSource([5])

        with (
            patch(VIDEO_CAPTURE, source),
            patch.object(
                extractor, "_reconnect_with_backoff", new_callable=AsyncMock, return_value=False
            ) as reconnect,
        ):
            await asyncio.wait_for(extractor._capture_loop(), timeout=10)

        reconnect.assert_awaited_once()
        assert [n for _, n, _, _ in recorder.frames] == [1, 3]
        # DISCONNECTED is reported, then the loop's exit settles on STOPPED
        assert recorder.statuses == [
            PipelineStatus.RUNNING,
            PipelineStatus.DISCONNECTED,
            PipelineStatus.STOPPED,
        ]
        assert extractor._capture is None
        assert source.opened[0].released

    async def test_source_that_never_opens_disconnects_without_reading(self) -> None:
        extractor = make_extractor(skip=2)
        recorder = Recorder(extractor)
        source = FakeSource([])

        with patch(VIDEO_CAPTURE, source):
            await asyncio.wait_for(extractor._capture_loop(), timeout=10)

        assert recorder.frames == []
        assert recorder.statuses == [
            PipelineStatus.RUNNING,
            PipelineStatus.DISCONNECTED,
            PipelineStatus.STOPPED,
        ]
        assert len(source.opened) == 1

    async def test_error_in_open_disconnects(self) -> None:
        extractor = make_extractor(skip=2)
        recorder = Recorder(extractor)

        def explode(url, *args):
            raise RuntimeError("backend crashed")

        with patch(VIDEO_CAPTURE, explode):
            await asyncio.wait_for(extractor._capture_loop(), timeout=10)

        assert recorder.statuses == [
            PipelineStatus.RUNNING,
            PipelineStatus.DISCONNECTED,
            PipelineStatus.STOPPED,
        ]


class TestStopMidLoop:
    """stop() during a run ends the task promptly and delivers nothing more."""

    async def test_stop_exits_promptly(self) -> None:
        extractor = FrameExtractor()
        recorder = Recorder(extractor)
        three = asyncio.Event()

        async def count(frame, seq, frame_number, fps) -> None:
            if seq >= 3:
                three.set()

        extractor.on_frame(count)
        source = FakeSource([10**9])  # endless for the test's purposes

        with patch(VIDEO_CAPTURE, source):
            extractor.start(
                FrameExtractorConfig(
                    source_url=URL, skip_frames=2, target_width=WIDTH,
                    target_height=HEIGHT, decode_workers=1,
                )
            )
            task = extractor._capture_task
            assert task is not None
            await asyncio.wait_for(three.wait(), timeout=10)
            extractor.stop()
            delivered = len(recorder.frames)
            await asyncio.wait([task], timeout=2)

        assert task.done()
        assert extractor.status == PipelineStatus.STOPPED
        assert extractor._capture is None
        assert source.opened[0].released
        assert len(recorder.frames) == delivered
        assert [n for _, n, _, _ in recorder.frames[:3]] == [1, 3, 5]

    async def test_restart_numbers_from_zero_again(self) -> None:
        # The counter is per run: a second start() on the same extractor
        # numbers frames from the start of the reopened source again.
        extractor = make_extractor(skip=3)
        recorder = Recorder(extractor)
        config = extractor._config
        assert config is not None
        source = FakeSource([10, 10], on_exhausted=extractor._stop_event.set)

        with patch(VIDEO_CAPTURE, source):
            for _ in range(2):
                with patch.object(
                    extractor, "_reconnect_with_backoff",
                    new_callable=AsyncMock, return_value=False,
                ):
                    extractor.start(config)
                    task = extractor._capture_task
                    assert task is not None
                    await asyncio.wait_for(task, timeout=10)

        assert recorder.frames == [
            (1, 2, 0, 2), (2, 5, 0, 5), (3, 8, 0, 8),
            (1, 2, 1, 2), (2, 5, 1, 5), (3, 8, 1, 8),
        ]


class TestValidateReturnsSourceUnavailable:
    """validate() reports an unusable source by return value, never raising."""

    async def test_unreachable_source_is_returned(self) -> None:
        source = FakeSource([])
        with patch(VIDEO_CAPTURE, source):
            result = await FrameExtractor().validate(URL)

        assert isinstance(result, SourceUnavailable)
        assert result.url == URL
        assert source.opened[0].released

    async def test_backend_error_is_returned_not_raised(self) -> None:
        def explode(url, *args):
            raise RuntimeError("no route to host")

        with patch(VIDEO_CAPTURE, explode):
            result = await FrameExtractor().validate(URL)

        assert isinstance(result, SourceUnavailable)
        assert "no route to host" in result.message
        assert result.url == URL

    async def test_reachable_source_validates(self) -> None:
        source = FakeSource([3])
        with patch(VIDEO_CAPTURE, source):
            assert await FrameExtractor().validate(URL) is None
        assert source.opened[0].released
