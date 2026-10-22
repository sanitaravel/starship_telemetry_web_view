"""The OpenCV capture handle of the frame extractor and its reconnection."""

import asyncio
import logging
import threading

import cv2
import numpy as np

from src.capture.config import FrameExtractorConfig
from src.capture.notifier import CaptureNotifier
from src.capture.reconnect import retry_with_backoff
from src.capture.source import open_capture, probe_frame_count
from src.core.enums import PipelineStatus


logger = logging.getLogger(__name__)


class CaptureConnection(CaptureNotifier):
    """Owns the main VideoCapture: locked reads, seeking, release and reconnection.

    The base of FrameExtractor (after CaptureNotifier), kept apart from the
    capture loop that decides which frames to read.
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
        self._stop_event: asyncio.Event = asyncio.Event()

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
