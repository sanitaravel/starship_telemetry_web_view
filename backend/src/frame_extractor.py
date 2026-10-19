"""Frame Extractor module for the Starship Telemetry system.

Captures frames from a video source at a configurable interval using
OpenCV VideoCapture. Supports async operation with callback registration,
automatic frame resizing to 1920×1080, and exponential backoff reconnection.
"""

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Awaitable

import cv2
import numpy as np

from src.enums import PipelineStatus
from src.parallel_decoder import ParallelFileDecoder


logger = logging.getLogger(__name__)


@dataclass
class FrameExtractorConfig:
    """Configuration for the frame extraction pipeline.

    Attributes:
        source_url: Video source URL (e.g., RTSP, HTTP stream, or file path).
        skip_frames: Process every Nth frame (default: 30, i.e., ~1 per second at 30fps).
        target_width: Target frame width after resize (default: 1920).
        target_height: Target frame height after resize (default: 1080).
        decode_workers: Most decoders to run side by side on a seekable source
            (see parallel_decoder), further capped at half the logical CPUs;
            1 decodes serially. Measured at skip 30 on a 16-thread CPU: on a
            busy 8 Mbit/s scene 8 decoders beat 4 by 10%; on a real 1.6 Mbit/s
            flight 3-8 decoders were within noise of each other, all 20-25%
            ahead of serial decoding.
    """

    source_url: str
    skip_frames: int = 30
    target_width: int = 1920
    target_height: int = 1080
    decode_workers: int = 8


# Fallback source FPS used when the video backend does not report a usable
# frame rate (e.g. some live streams return 0 for CAP_PROP_FPS). 29.97 is the
# standard NTSC broadcast rate used by the SpaceX webcast.
DEFAULT_SOURCE_FPS: float = 30000.0 / 1001.0

# Parallel decoding (see parallel_decoder). Each unit of work starts with a
# seek, which decodes from the preceding keyframe -- up to one keyframe
# interval, ~2 s on YouTube-style encodes -- so units span 8 s of source, and
# parallel decoding is only used when a unit of at least 4 s still fits the
# memory budget. Below that skip interval decoding is not the bottleneck
# anyway: measured, the serial extractor delivers ~350 frames/s at skip 1.
_PARALLEL_CHUNK_SOURCE_FRAMES = 240
_PARALLEL_MIN_CHUNK_SOURCE_FRAMES = 120
# Decoded 1080p frames held ahead of the pipeline, ~6 MB each.
_PARALLEL_MAX_BUFFERED = 48


@dataclass
class ConnectionError:
    """Describes a failure to connect to a video source.

    Attributes:
        message: Human-readable error description.
        url: The URL that was attempted.
    """

    message: str
    url: str


class FrameExtractor:
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
        self._status: PipelineStatus = PipelineStatus.STOPPED
        self._config: FrameExtractorConfig | None = None
        self._capture: cv2.VideoCapture | None = None
        # Held around every grab/read and around release: releasing a capture
        # while another thread decodes from it aborts the process inside FFmpeg
        # ("Assertion fctx->async_lock failed")
        self._capture_lock = threading.Lock()
        self._capture_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._sequence_number: int = 0
        self._source_fps: float = DEFAULT_SOURCE_FPS
        self._frame_callbacks: list[
            Callable[[np.ndarray, int, int, float], Awaitable[None]]
        ] = []
        self._status_callbacks: list[Callable[[PipelineStatus], Awaitable[None]]] = []
        self._stop_event: asyncio.Event = asyncio.Event()
        # Parallel decoding of a seekable source. _frame_count is the source's
        # length once it has been found to seek accurately, 0 if it cannot,
        # None until checked (once per start()).
        self._decoder: ParallelFileDecoder | None = None
        self._frame_count: int | None = None

    @property
    def status(self) -> PipelineStatus:
        """Current pipeline status."""
        return self._status

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

    async def validate(self, url: str) -> None | ConnectionError:
        """Check whether the video source is reachable and active.

        Attempts to open the video source URL with OpenCV and read a single
        frame to verify the source is active and producing video.

        Args:
            url: Video source URL to validate.

        Returns:
            None if the source is reachable and active, or a ConnectionError
            with a descriptive message if not.
        """
        if not url or not url.strip():
            return ConnectionError(
                message="Video source URL cannot be empty.",
                url=url,
            )

        cap: cv2.VideoCapture | None = None
        try:
            # Run blocking OpenCV operations in a thread to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            cap = await loop.run_in_executor(None, cv2.VideoCapture, url)

            if not cap.isOpened():
                return ConnectionError(
                    message=f"Unable to open video source. The URL may be unreachable or invalid.",
                    url=url,
                )

            # Try reading a single frame to verify the stream is active
            ret, frame = await loop.run_in_executor(None, cap.read)
            if not ret:
                return ConnectionError(
                    message="Video source opened but no frames could be read. The stream may not be active.",
                    url=url,
                )

            # Check source resolution — warn if not 1080p (frames will be resized)
            source_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            source_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if source_w != 1920 or source_h != 1080:
                logger.warning(
                    f"Video source is {source_w}x{source_h}, not 1920x1080. "
                    f"Frames will be resized which may reduce OCR and detection accuracy. "
                    f"Use a 1080p stream for best results."
                )

            return None

        except Exception as e:
            return ConnectionError(
                message=f"Error validating video source: {str(e)}",
                url=url,
            )
        finally:
            if cap is not None:
                cap.release()

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

    def on_frame(
        self, callback: Callable[[np.ndarray, int, int, float], Awaitable[None]]
    ) -> None:
        """Register a callback for each captured frame.

        The callback receives a BGR numpy array (1920×1080), the processed-frame
        sequence number, the absolute source frame number (its 0-based index in
        the original video stream), and the source frame rate in FPS.

        Args:
            callback: Async function accepting
                (frame: np.ndarray, seq: int, frame_number: int, source_fps: float).
        """
        self._frame_callbacks.append(callback)

    def on_status_change(self, callback: Callable[[PipelineStatus], Awaitable[None]]) -> None:
        """Register a callback for pipeline status transitions.

        The callback is invoked whenever the pipeline status changes
        (e.g., STOPPED → RUNNING, RUNNING → RECONNECTING).

        Args:
            callback: Async function accepting (status: PipelineStatus).
        """
        self._status_callbacks.append(callback)

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

    @staticmethod
    def _parse_source_fps(reported_fps: object) -> float:
        """Coerce a backend-reported frame rate into a usable FPS value.

        OpenCV backends may report 0, NaN, or (in tests) non-numeric values for
        CAP_PROP_FPS. Any of these are treated as "unknown" and fall back to
        DEFAULT_SOURCE_FPS so downstream timing stays well-defined.

        Args:
            reported_fps: The raw value returned by ``VideoCapture.get(CAP_PROP_FPS)``.

        Returns:
            A positive, finite frame rate in frames per second.
        """
        try:
            fps = float(reported_fps)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            fps = 0.0

        if fps > 0 and not np.isnan(fps) and not np.isinf(fps):
            return fps

        logger.warning(
            "Video source did not report a usable frame rate "
            "(CAP_PROP_FPS=%r). Falling back to %.3f FPS for timing.",
            reported_fps,
            DEFAULT_SOURCE_FPS,
        )
        return DEFAULT_SOURCE_FPS

    async def _set_status(self, new_status: PipelineStatus) -> None:
        """Update pipeline status and notify registered callbacks.

        Args:
            new_status: The new pipeline status to transition to.
        """
        if self._status == new_status:
            return

        old_status = self._status
        self._status = new_status
        logger.info(f"Pipeline status: {old_status.value} → {new_status.value}")

        for callback in self._status_callbacks:
            try:
                await callback(new_status)
            except Exception as e:
                logger.error(f"Error in status change callback: {e}")

    async def _notify_frame(
        self, frame: np.ndarray, seq: int, frame_number: int, source_fps: float
    ) -> None:
        """Invoke all registered frame callbacks.

        Args:
            frame: The captured and resized BGR frame.
            seq: The processed-frame sequence number.
            frame_number: Absolute source frame index in the original stream.
            source_fps: Source video frame rate in frames per second.
        """
        for callback in self._frame_callbacks:
            try:
                await callback(frame, seq, frame_number, source_fps)
            except Exception as e:
                logger.error(f"Error in frame callback: {e}")

    async def _capture_loop(self) -> None:
        """Main async capture loop.

        Opens the video source, reads every frame, but only processes
        every Nth frame (skip_frames). Handles disconnection with
        exponential backoff reconnection.
        """
        assert self._config is not None

        await self._set_status(PipelineStatus.RUNNING)

        loop = asyncio.get_event_loop()
        backoff_s = self._INITIAL_BACKOFF_S
        frame_counter = 0

        try:
            # Initial connection
            self._capture = await loop.run_in_executor(
                None, cv2.VideoCapture, self._config.source_url
            )

            if not self._capture.isOpened():
                logger.error(f"Failed to open video source: {self._config.source_url}")
                await self._set_status(PipelineStatus.DISCONNECTED)
                return

            # Request 1080p from source (hint — not all backends honor this)
            self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._config.target_width)
            self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._config.target_height)

            # Read the source frame rate. Some backends/streams report 0, NaN,
            # or an unparseable value; fall back to the NTSC default in that case.
            self._source_fps = self._parse_source_fps(
                self._capture.get(cv2.CAP_PROP_FPS)
            )
            logger.info("Source frame rate: %.3f FPS.", self._source_fps)

            actual_w = self._capture.get(cv2.CAP_PROP_FRAME_WIDTH)
            actual_h = self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
            if actual_w != self._config.target_width or actual_h != self._config.target_height:
                logger.warning(
                    f"Requested {self._config.target_width}x{self._config.target_height} "
                    f"but source provides {int(actual_w)}x{int(actual_h)}. "
                    f"Frames will be resized."
                )
            else:
                logger.info(
                    f"Source confirmed at {int(actual_w)}x{int(actual_h)}."
                )

            # Serial decoding keeps the main capture at frame_counter; parallel
            # decoding reads through its own captures and leaves it behind.
            capture_in_sync = True

            # Main frame capture loop
            while not self._stop_event.is_set():
                skip = self._config.skip_frames
                decode_start = time.monotonic()
                decoder = await self._decoder_for(frame_counter, skip)
                if decoder is not None:
                    capture_in_sync = False
                    result = await loop.run_in_executor(None, decoder.next_frame)
                    if result is None:
                        ret, frame = False, None
                    else:
                        # frame_counter becomes the frame's number; +1 below
                        ret = True
                        frame_counter, frame = result
                else:
                    if not capture_in_sync:
                        await loop.run_in_executor(None, self._seek_capture, frame_counter)
                        capture_in_sync = True
                    # Grab the frames that will be skipped and read only the
                    # next processed one, in a single executor hop.
                    to_skip = skip - 1 - (frame_counter % skip)
                    grabbed, ret, frame = await loop.run_in_executor(
                        None, self._advance, self._capture, to_skip
                    )
                    frame_counter += grabbed

                if self._stop_event.is_set():
                    break

                if not ret or frame is None or frame.size == 0:
                    await loop.run_in_executor(None, self._close_decoder)
                    # Source disconnected — attempt reconnection
                    success = await self._reconnect_with_backoff()
                    if not success:
                        # All reconnection attempts exhausted
                        await self._set_status(PipelineStatus.DISCONNECTED)
                        return
                    # Reset backoff on successful reconnection
                    backoff_s = self._INITIAL_BACKOFF_S
                    frame_counter = 0
                    capture_in_sync = True
                    continue

                # Reset backoff on each successful frame read
                backoff_s = self._INITIAL_BACKOFF_S
                frame_counter += 1

                # Only process every Nth frame (skip_frames may have changed
                # while the grab batch was running)
                if frame_counter % self._config.skip_frames != 0:
                    continue

                logger.debug(
                    "Frame capture completed",
                    extra={"duration_ms": int((time.monotonic() - decode_start) * 1000)},
                )

                # Resize frame to target resolution (skip if already correct)
                h, w = frame.shape[:2]
                if w == self._config.target_width and h == self._config.target_height:
                    resized = frame
                else:
                    resized = self.resize_frame(
                        frame, self._config.target_width, self._config.target_height
                    )

                # Increment sequence and notify. frame_counter counts every
                # frame read from the source, so its 0-based index is the
                # absolute frame number in the original stream.
                self._sequence_number += 1
                frame_number = frame_counter - 1
                await self._notify_frame(
                    resized, self._sequence_number, frame_number, self._source_fps
                )

                # Yield control briefly to check for stop event
                await asyncio.sleep(0)

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

    def _advance(
        self, capture: cv2.VideoCapture, to_skip: int
    ) -> tuple[int, bool, np.ndarray | None]:
        """Grab ``to_skip`` frames, then read one frame.

        Runs in the executor. ``grab()`` still decodes -- H.264 frames are
        predicted from their neighbours -- but skips the colour conversion that
        ``read()`` adds, which measured at 73-94% of read's cost on 1080p.

        Returns:
            (frames grabbed before the read, read success, decoded frame).
        """
        grabbed = 0
        for _ in range(to_skip):
            # Lock per grab, so stop() can release the capture between grabs
            with self._capture_lock:
                if self._stop_event.is_set() or not capture.grab():
                    return grabbed, False, None
            grabbed += 1
        with self._capture_lock:
            if self._stop_event.is_set():
                return grabbed, False, None
            ret, frame = capture.read()
        return grabbed, ret, frame

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

        plan = self._parallel_plan(skip)
        if plan is None:
            return None
        if self._frame_count is None:
            self._frame_count = await loop.run_in_executor(None, self._probe_seekable)
        if not self._frame_count:
            return None

        assert self._config is not None
        chunk, workers, threads = plan
        # Same frames the serial path would keep: those with
        # (frame_number + 1) a multiple of skip
        first = frame_counter + skip - 1 - frame_counter % skip
        self._decoder = ParallelFileDecoder(
            self._config.source_url,
            first_frame=first,
            skip=skip,
            workers=workers,
            chunk_frames=chunk,
            max_buffered=(workers + 1) * chunk,
            threads_per_decoder=threads,
            reported_frame_count=self._frame_count,
        )
        logger.info(
            "Decoding in parallel from frame %d: %d decoders x %d threads, "
            "%d kept frames per chunk.",
            first, workers, threads, chunk,
        )
        return self._decoder

    def _parallel_plan(self, skip: int) -> tuple[int, int, int] | None:
        """Return (kept frames per chunk, decoders, FFmpeg threads per decoder).

        Uses as many decoders as the memory budget allows while each chunk
        still spans enough source to pay for its seek: more decoders mean
        shorter chunks, so at shorter skip intervals fewer of them run. None
        when parallel decoding is turned off or would not pay at this skip
        interval even with two decoders (see _PARALLEL_* above).
        """
        assert self._config is not None
        # More decoders than physical cores only fight over them
        most = min(self._config.decode_workers, (os.cpu_count() or 2) // 2)
        for workers in range(most, 1, -1):
            chunk = min(
                -(-_PARALLEL_CHUNK_SOURCE_FRAMES // skip),
                _PARALLEL_MAX_BUFFERED // (workers + 1),
            )
            if chunk * skip >= _PARALLEL_MIN_CHUNK_SOURCE_FRAMES:
                threads = max(1, (os.cpu_count() or 2) // workers)
                return chunk, workers, threads
        return None

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

        probe = cv2.VideoCapture(self._config.source_url, cv2.CAP_FFMPEG)
        try:
            total = int(probe.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 1:
                return 0
            for target in (total // 2, 0):
                if not probe.set(cv2.CAP_PROP_POS_FRAMES, target):
                    return 0
                if int(probe.get(cv2.CAP_PROP_POS_FRAMES)) != target:
                    return 0
            return total
        except Exception as e:
            logger.warning(f"Seek check failed, decoding serially: {e}")
            return 0
        finally:
            probe.release()

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
        loop = asyncio.get_event_loop()
        backoff_s = self._INITIAL_BACKOFF_S

        await self._set_status(PipelineStatus.RECONNECTING)
        self._release_capture()

        while not self._stop_event.is_set():
            logger.info(f"Reconnecting in {backoff_s:.1f}s...")

            # Wait for backoff duration (or stop event)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=backoff_s
                )
                # Stop was requested during backoff
                return False
            except asyncio.TimeoutError:
                # Backoff elapsed, attempt reconnection
                pass

            # Attempt to reopen the video source
            try:
                self._capture = await loop.run_in_executor(
                    None, cv2.VideoCapture, self._config.source_url
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

            # Increase backoff with exponential growth, capped at max
            backoff_s = min(backoff_s * self._BACKOFF_MULTIPLIER, self._MAX_BACKOFF_S)

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
