"""Parallel decoding of seekable video sources (files and VOD).

With skip_frames=30 the extractor decodes 30 frames for every one it keeps:
H.264 frames are predicted from their neighbours, so the skipped ones cannot
be left undecoded, and after the OCR work was batched that decoding became the
pipeline's limit -- measured at 1.5-2k decoded frames/s on a 16-thread CPU, or
50-65 kept frames/s on a busy scene. FFmpeg already spreads one stream over
every core, so more threads per decoder do not help, but separate stretches of
a file can be decoded side by side. Measured on an 8 Mbit/s 1080p H.264 file
at skip 30, whole pipeline: one decoder 45 kept frames/s, four of 4 threads
62, eight of 2 threads 67. (NVDEC, through OpenCV's D3D11 path or ffmpeg's
CUDA one, managed 29-31 on its own.)

The source is cut into chunks of consecutive kept frames. Each worker owns a
VideoCapture, seeks to the start of the chunk it claims and walks it exactly
as the serial extractor would -- skip-1 grabs, then a read -- so the frames
and frame numbers are the same as serial decoding gives. The consumer takes
them back in order.

Only for sources that seek accurately: see FrameExtractor._plan_parallel. A
live stream arrives in real time, so it never needs this.
"""

import logging
import threading

import cv2
import numpy as np


logger = logging.getLogger(__name__)


class ParallelFileDecoder:
    """Decodes every ``skip``-th frame of a seekable source on several threads.

    Frames come back from ``next_frame`` in source order. Decoded frames that
    are not yet wanted are held in memory, at most ``max_buffered`` of them.
    """

    def __init__(
        self,
        source_url: str,
        first_frame: int,
        skip: int,
        workers: int,
        chunk_frames: int,
        max_buffered: int,
        threads_per_decoder: int,
        reported_frame_count: int = 0,
    ) -> None:
        """Start the worker threads.

        Args:
            source_url: Source to open, once per worker.
            first_frame: Source index of the first frame to keep.
            skip: Keep every ``skip``-th frame after ``first_frame``.
            workers: Decoder threads, each with its own VideoCapture.
            chunk_frames: Kept frames per unit of work. Each unit starts with a
                seek, which decodes from the preceding keyframe, so a unit
                should span several keyframe intervals.
            max_buffered: Most decoded frames held ahead of the consumer.
            threads_per_decoder: FFmpeg threads for each VideoCapture.
            reported_frame_count: The source's frame count, used only to tell
                a seek past the end (expected) from one that missed (logged).
        """
        self._url = source_url
        self._first = first_frame
        self._skip = skip
        self._chunk = chunk_frames
        self._max_buffered = max_buffered
        self._threads_per_decoder = threads_per_decoder
        self._reported_frame_count = reported_frame_count

        self._cond = threading.Condition()
        self._frames: dict[int, np.ndarray] = {}  # kept-frame index -> frame
        self._next_needed = 0  # index of the next frame next_frame() returns
        self._next_chunk = 0
        self._end: int | None = None  # first kept-frame index past the source
        self._stopped = False
        self.peak_buffered = 0

        self._threads = [
            threading.Thread(target=self._work, name=f"decode-{i}", daemon=True)
            for i in range(workers)
        ]
        for thread in self._threads:
            thread.start()

    @property
    def skip(self) -> int:
        """The kept-frame interval this decoder was started with."""
        return self._skip

    def frame_number(self, index: int) -> int:
        """Source index of the ``index``-th kept frame."""
        return self._first + index * self._skip

    def next_frame(self) -> tuple[int, np.ndarray] | None:
        """Block until the next kept frame is decoded.

        Returns:
            (source frame number, BGR frame), or None once the source has run
            out or ``close`` was called.
        """
        with self._cond:
            while True:
                index = self._next_needed
                frame = self._frames.pop(index, None)
                if frame is not None:
                    self._next_needed += 1
                    self._cond.notify_all()
                    return self.frame_number(index), frame
                if self._stopped or (self._end is not None and index >= self._end):
                    return None
                self._cond.wait()

    def close(self) -> None:
        """Stop the workers and drop any frames they decoded ahead.

        Each worker releases its own VideoCapture: releasing one while another
        thread decodes from it aborts the process inside FFmpeg.
        """
        with self._cond:
            self._stopped = True
            self._frames.clear()
            self._cond.notify_all()
        for thread in self._threads:
            thread.join(timeout=5.0)

    def _work(self) -> None:
        """Claim chunks in order and decode them until the source runs out."""
        capture: cv2.VideoCapture | None = None
        start = 0
        try:
            while True:
                with self._cond:
                    start = self._next_chunk * self._chunk
                    if self._stopped or (self._end is not None and start >= self._end):
                        return
                    self._next_chunk += 1
                # A chunk read from the start needs a capture that has not
                # moved yet (see _reads_from_start)
                if capture is not None and self._reads_from_start(self.frame_number(start)):
                    capture.release()
                    capture = None
                if capture is None:
                    capture = cv2.VideoCapture(
                        self._url,
                        cv2.CAP_FFMPEG,
                        [cv2.CAP_PROP_N_THREADS, self._threads_per_decoder],
                    )
                    if not capture.isOpened():
                        self._end_at(start, "worker could not open the source")
                        return
                self._decode_chunk(capture, start)
        except Exception as exc:  # noqa: BLE001 - surfaces as the end of the source
            # The failed chunk may be half decoded, so the run ends at its
            # start; frames before it, from other workers, still go out.
            logger.error("Parallel decoder worker failed: %s", exc, exc_info=exc)
            self._end_at(start, None)
        finally:
            if capture is not None:
                capture.release()

    def _decode_chunk(self, capture: cv2.VideoCapture, start: int) -> None:
        """Decode the kept frames ``start .. start + chunk - 1``."""
        target = self.frame_number(start)
        if self._reads_from_start(target):
            # The capture is freshly opened, at frame 0
            for _ in range(target):
                if not capture.grab():
                    self._end_at(start, None)
                    return
            landed = True
        else:
            landed = capture.set(cv2.CAP_PROP_POS_FRAMES, target) and (
                int(capture.get(cv2.CAP_PROP_POS_FRAMES)) == target
            )
        if not landed:
            past_end = 0 < self._reported_frame_count <= target
            self._end_at(start, None if past_end else f"seek to frame {target} missed")
            return

        for index in range(start, start + self._chunk):
            if not self._wait_for_room(index):
                return
            if index > start:
                for _ in range(self._skip - 1):
                    if not capture.grab():
                        self._end_at(index, None)
                        return
            ok, frame = capture.read()
            if not ok or frame is None:
                self._end_at(index, None)
                return
            with self._cond:
                if self._stopped:
                    return
                self._frames[index] = frame
                self.peak_buffered = max(self.peak_buffered, len(self._frames))
                self._cond.notify_all()

    def _reads_from_start(self, target: int) -> bool:
        """Whether to reach ``target`` by decoding from frame 0, not seeking.

        Seeks near the start of a source are not trustworthy. On an HLS VOD
        whose video starts behind its first timestamps, every seek to frames
        0-44 decoded frame 44 or 45 while POS_FRAMES still read back the
        target; from frame 50 on they were exact. Decoding from the start
        costs at most one chunk's span, which a seek's run-up from the
        keyframe costs anyway.
        """
        return target < self._chunk * self._skip

    def _wait_for_room(self, index: int) -> bool:
        """Hold a worker that has run too far ahead of the consumer.

        Returns False when the frame is no longer wanted.
        """
        with self._cond:
            while (
                not self._stopped
                and (self._end is None or index < self._end)
                and index >= self._next_needed + self._max_buffered
            ):
                self._cond.wait()
            return not self._stopped and (self._end is None or index < self._end)

    def _end_at(self, index: int, problem: str | None) -> None:
        """Record that the source ends before kept frame ``index``.

        Frames past the earliest recorded end are never handed out, so a
        failure truncates the run rather than leaving a gap in it.
        """
        with self._cond:
            if self._end is None or index < self._end:
                self._end = index
            self._cond.notify_all()
        if problem is not None:
            logger.warning(
                "Parallel decoding stops at frame %d: %s.", self.frame_number(index), problem
            )
