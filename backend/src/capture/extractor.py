"""Frame Extractor module for the Starship Telemetry system.

Captures frames from a video source at a configurable interval using
OpenCV VideoCapture. Supports async operation with callback registration,
automatic frame resizing to 1920×1080, and exponential backoff reconnection.
"""

import asyncio
import logging
import threading
import time

import cv2
import numpy as np

from src.capture.config import DEFAULT_SOURCE_FPS, FrameExtractorConfig, SourceUnavailable
from src.capture.notifier import CaptureNotifier
from src.capture.parallel_decoder import ParallelFileDecoder
from src.capture.planning import parallel_plan, start_decoder
from src.capture.reconnect import retry_with_backoff
from src.capture.source import (
    advance,
    configure_capture,
    open_capture,
    probe_frame_count,
    validate_source,
)
from src.core.enums import PipelineStatus


logger = logging.getLogger(__name__)


class FrameExtractor(CaptureNotifier):
    """Captures frames from a video source using OpenCV VideoCapture.

    Provides async frame capture at a configurable interval, automatic resize
    to target resolution, callback-based notification for frames and status
    changes, and exponential backoff reconnection on disconnection.

    Usage:
        extractor = FrameExtractor()
        extractor.on_frame(my_frame_handler)
        extractor.on_status_change(my_status_handler)

        error = await extractor.validate(url)
        if error is None:
            extractor.start(FrameExtractorConfig(source_url=url))
            # ... later ...
            extractor.stop()
    """

    # Reconnection backoff constants
    _INITIAL_BACKOFF_S: float = 1.0
    _MAX_BACKOFF_S: float = 30.0
    _BACKOFF_MULTIPLIER: float = 2.0

    def __init__(self) -> None:
        super().__init__()
        self._config: FrameExtractorConfig | None = None
        self._capture: cv2.VideoCapture | None = None
        # Held around every grab/read and around release: releasing a capture
        # while another thread decodes from it aborts the process inside FFmpeg
        # ("Assertion fctx->async_lock failed")
        self._capture_lock = threading.Lock()
        self._capture_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._sequence_number: int = 0
        self._source_fps: float = DEFAULT_SOURCE_FPS
        self._stop_event: asyncio.Event = asyncio.Event()
        # Parallel decoding of a seekable source. _frame_count is the source's
        # length once it has been found to seek accurately, 0 if it cannot,
        # None until checked (once per start()).
        self._decoder: ParallelFileDecoder | None = None
        self._frame_count: int | None = None
        # Capture loop position: frames read from the source so far, and
        # whether the main capture is at that frame. Serial decoding keeps
        # the main capture at _frame_counter; parallel decoding reads through
        # its own captures and leaves it behind.
        self._frame_counter: int = 0
        self._capture_in_sync: bool = True

    @property
    def sequence_number(self) -> int:
        """Current frame sequence number."""
        return self._sequence_number

    @property
    def source_fps(self) -> float:
        """Frame rate of the active video source, in frames per second.

        Falls back to DEFAULT_SOURCE_FPS when the backend does not report a
        usable value.
        """
        return self._source_fps

    async def validate(self, url: str) -> None | SourceUnavailable:
        """Check whether the video source is reachable and active.

        Returns:
            None if the source is reachable and active, or a SourceUnavailable
            with a descriptive message if not (see source.validate_source).
        """
        return await validate_source(url)

    def start(self, config: FrameExtractorConfig) -> None:
        """Begin frame capture at the configured interval.

        Starts an async background task that captures frames from the video
        source and invokes registered callbacks. The pipeline transitions
        to RUNNING status.

        Args:
            config: Frame extraction configuration including source URL and interval.

        Raises:
            RuntimeError: If the pipeline is already running.
        """
        if self._status == PipelineStatus.RUNNING:
            raise RuntimeError("Frame extractor is already running.")

        self._config = config
        self._stop_event.clear()
        self._sequence_number = 0
        self._frame_count = None

        # Start the capture loop as an async task
        self._capture_task = asyncio.ensure_future(self._capture_loop())

    def stop(self) -> None:
        """Stop frame capture immediately.

        Signals the capture loop to stop, releases the video capture resource,
        and transitions to STOPPED status. Safe to call even if already stopped.

        The capture loop itself handles the final status transition to STOPPED
        when it detects the stop event. This method just signals and cleans up.
        """
        self._stop_event.set()

        if self._capture_task is not None and not self._capture_task.done():
            self._capture_task.cancel()
            self._capture_task = None

        self._close_decoder()
        self._release_capture()
        self._status = PipelineStatus.STOPPED

    def resize_frame(self, frame: np.ndarray, target_width: int, target_height: int) -> np.ndarray:
        """Resize a frame to the target resolution.

        Uses cv2.resize with INTER_LINEAR interpolation to produce a frame
        of exactly target_width × target_height pixels regardless of input size.

        Args:
            frame: Input BGR numpy array of any resolution.
            target_width: Desired output width in pixels.
            target_height: Desired output height in pixels.

        Returns:
            Resized BGR numpy array with shape (target_height, target_width, 3).
        """
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_LINEAR)

    async def _capture_loop(self) -> None:
        """Main async capture loop.

        Opens the video source, reads every frame, but only processes
        every Nth frame (skip_frames). Handles disconnection with
        exponential backoff reconnection.
        """
        assert self._config is not None

        await self._set_status(PipelineStatus.RUNNING)

        loop = asyncio.get_event_loop()
        self._frame_counter = 0

        try:
            if not await self._open_source(loop):
                return
            self._capture_in_sync = True
            await self._read_frames(loop)

        except asyncio.CancelledError:
            logger.info("Capture loop cancelled.")
        except Exception as e:
            logger.error(f"Unexpected error in capture loop: {e}")
            await self._set_status(PipelineStatus.DISCONNECTED)
        finally:
            self._close_decoder()
            self._release_capture()
            if self._status != PipelineStatus.STOPPED:
                await self._set_status(PipelineStatus.STOPPED)

    async def _open_source(self, loop: asyncio.AbstractEventLoop) -> bool:
        """Open the main capture and read its frame rate.

        Returns:
            False, after moving to DISCONNECTED, if the source would not open.
        """
        assert self._config is not None
        self._capture = await loop.run_in_executor(
            None, open_capture, self._config.source_url
        )

        if not self._capture.isOpened():
            logger.error(f"Failed to open video source: {self._config.source_url}")
            await self._set_status(PipelineStatus.DISCONNECTED)
            return False

        self._source_fps = configure_capture(
            self._capture, self._config.target_width, self._config.target_height
        )
        return True

    async def _read_frames(self, loop: asyncio.AbstractEventLoop) -> None:
        """Read the source until stopped, dispatching every Nth frame.

        Returns when stopped, or after moving to DISCONNECTED when the source
        is lost and cannot be reconnected.
        """
        assert self._config is not None
        while not self._stop_event.is_set():
            skip = self._config.skip_frames
            decode_start = time.monotonic()
            ret, frame = await self._read_next(loop, skip)

            if self._stop_event.is_set():
                break

            if not ret or frame is None or frame.size == 0:
                if not await self._recover_source(loop):
                    return
                continue

            self._frame_counter += 1

            # Only process every Nth frame (skip_frames may have changed
            # while the grab batch was running)
            if self._frame_counter % self._config.skip_frames != 0:
                continue

            logger.debug(
                "Frame capture completed",
                extra={"duration_ms": int((time.monotonic() - decode_start) * 1000)},
            )
            await self._dispatch_frame(frame)

            # Yield control briefly to check for stop event
            await asyncio.sleep(0)

    async def _read_next(
        self, loop: asyncio.AbstractEventLoop, skip: int
    ) -> tuple[bool, np.ndarray | None]:
        """Read the next frame to keep, from the parallel decoder or serially.

        Advances _frame_counter past the frames skipped on the way; the
        caller counts the returned frame itself.
        """
        decoder = await self._decoder_for(self._frame_counter, skip)
        if decoder is not None:
            self._capture_in_sync = False
            result = await loop.run_in_executor(None, decoder.next_frame)
            if result is None:
                return False, None
            # _frame_counter becomes the frame's number; the caller adds 1
            self._frame_counter, frame = result
            return True, frame

        if not self._capture_in_sync:
            await loop.run_in_executor(None, self._seek_capture, self._frame_counter)
            self._capture_in_sync = True
        # Grab the frames that will be skipped and read only the
        # next processed one, in a single executor hop.
        to_skip = skip - 1 - (self._frame_counter % skip)
        # Under the capture lock per grab and read (see source.advance).
        grabbed, ret, frame = await loop.run_in_executor(
            None, advance, self._capture, to_skip, self._capture_lock, self._stop_event
        )
        self._frame_counter += grabbed
        return ret, frame

    async def _recover_source(self, loop: asyncio.AbstractEventLoop) -> bool:
        """Handle a lost source: stop parallel decoding and reconnect.

        Returns:
            True to carry on from frame 0 of the reopened source, False after
            moving to DISCONNECTED when reconnection gave up.
        """
        await loop.run_in_executor(None, self._close_decoder)
        # Source disconnected — attempt reconnection
        success = await self._reconnect_with_backoff()
        if not success:
            # All reconnection attempts exhausted
            await self._set_status(PipelineStatus.DISCONNECTED)
            return False
        self._frame_counter = 0
        self._capture_in_sync = True
        return True

    async def _dispatch_frame(self, frame: np.ndarray) -> None:
        """Resize a kept frame and hand it to the frame callbacks."""
        assert self._config is not None
        # Resize frame to target resolution (skip if already correct)
        h, w = frame.shape[:2]
        if w == self._config.target_width and h == self._config.target_height:
            resized = frame
        else:
            resized = self.resize_frame(
                frame, self._config.target_width, self._config.target_height
            )

        # Increment sequence and notify. _frame_counter counts every
        # frame read from the source, so its 0-based index is the
        # absolute frame number in the original stream.
        self._sequence_number += 1
        frame_number = self._frame_counter - 1
        await self._notify_frame(
            resized, self._sequence_number, frame_number, self._source_fps
        )

    async def _decoder_for(
        self, frame_counter: int, skip: int
    ) -> ParallelFileDecoder | None:
        """Return the parallel decoder to read from, or None to decode serially.

        Starts one when the source seeks accurately and the skip interval is
        long enough to pay for it, and restarts it from ``frame_counter`` when
        the skip interval changes mid-run.
        """
        if self._decoder is not None and self._decoder.skip == skip:
            return self._decoder

        loop = asyncio.get_running_loop()
        if self._decoder is not None:
            await loop.run_in_executor(None, self._close_decoder)

        # (kept frames per chunk, decoders, FFmpeg threads per decoder), or None
        # when parallel decoding is off or would not pay at this skip interval
        assert self._config is not None
        plan = parallel_plan(self._config.decode_workers, skip)
        if plan is None:
            return None
        if self._frame_count is None:
            self._frame_count = await loop.run_in_executor(None, self._probe_seekable)
        if not self._frame_count:
            return None

        self._decoder = start_decoder(
            self._config.source_url, frame_counter, skip, plan, self._frame_count
        )
        return self._decoder

    def _probe_seekable(self) -> int:
        """Return the source's frame count if it seeks accurately, else 0.

        Runs in the executor. Only FFmpeg-backed sources qualify, so the
        parallel decoders decode exactly what the serial path would; a live
        stream reports no frame count. The check uses a capture of its own,
        leaving the main one where it is.
        """
        assert self._config is not None
        try:
            with self._capture_lock:
                backend = self._capture.getBackendName() if self._capture is not None else ""
        except Exception:
            return 0
        if backend != "FFMPEG":
            return 0
        return probe_frame_count(self._config.source_url)

    def _seek_capture(self, frame_number: int) -> None:
        """Move the main capture to ``frame_number`` (runs in the executor).

        Used when switching back from parallel to serial decoding, which only
        happens on a source that passed _probe_seekable.
        """
        with self._capture_lock:
            if self._capture is not None:
                self._capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)

    def _close_decoder(self) -> None:
        """Stop the parallel decoder, if one is running."""
        decoder, self._decoder = self._decoder, None
        if decoder is not None:
            decoder.close()

    def _locked_read(self, capture: cv2.VideoCapture) -> tuple[bool, np.ndarray | None]:
        """capture.read() under the capture lock (runs in the executor)."""
        with self._capture_lock:
            return capture.read()

    async def _reconnect_with_backoff(self) -> bool:
        """Attempt to reconnect to the video source with exponential backoff.

        Tries reconnection with delays of 1s, 2s, 4s, 8s, ..., up to a
        maximum of 30s per attempt. Continues until reconnection succeeds
        or the stop event is set.

        Returns:
            True if reconnection succeeded, False if stop was requested
            or max backoff reached without success after repeated attempts.
        """
        assert self._config is not None

        await self._set_status(PipelineStatus.RECONNECTING)
        self._release_capture()

        return await retry_with_backoff(
            self._reopen_capture,
            self._stop_event,
            self._INITIAL_BACKOFF_S,
            self._MAX_BACKOFF_S,
            self._BACKOFF_MULTIPLIER,
        )

    async def _reopen_capture(self) -> bool:
        """One reconnection attempt: reopen the source and read a frame.

        Returns:
            True, after moving back to RUNNING, if a frame could be read.
        """
        assert self._config is not None
        loop = asyncio.get_event_loop()
        try:
            self._capture = await loop.run_in_executor(
                None, open_capture, self._config.source_url
            )

            if self._capture.isOpened():
                ret, _ = await loop.run_in_executor(None, self._locked_read, self._capture)
                if ret:
                    logger.info("Reconnection successful.")
                    await self._set_status(PipelineStatus.RUNNING)
                    return True
                else:
                    self._release_capture()
            else:
                self._release_capture()

        except Exception as e:
            logger.warning(f"Reconnection attempt failed: {e}")
            self._release_capture()
        return False

    def _release_capture(self) -> None:
        """Release the OpenCV VideoCapture resource if held.

        Waits for an in-flight grab or read to finish first (see _capture_lock).
        """
        with self._capture_lock:
            if self._capture is not None:
                try:
                    self._capture.release()
                except Exception as e:
                    logger.warning(f"Error releasing video capture: {e}")
                finally:
                    self._capture = None
