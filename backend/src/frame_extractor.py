"""Frame Extractor module for the Starship Telemetry system.

Captures frames from a video source at a configurable interval using
OpenCV VideoCapture. Supports async operation with callback registration,
automatic frame resizing to 1920×1080, and exponential backoff reconnection.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

import cv2
import numpy as np

from src.enums import PipelineStatus


logger = logging.getLogger(__name__)


@dataclass
class FrameExtractorConfig:
    """Configuration for the frame extraction pipeline.

    Attributes:
        source_url: Video source URL (e.g., RTSP, HTTP stream, or file path).
        interval_ms: Interval between frame captures in milliseconds (default: 1000ms = 1 fps).
        target_width: Target frame width after resize (default: 1920).
        target_height: Target frame height after resize (default: 1080).
    """

    source_url: str
    interval_ms: int = 1000
    target_width: int = 1920
    target_height: int = 1080


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
        self._capture_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._sequence_number: int = 0
        self._frame_callbacks: list[Callable[[np.ndarray, int], Awaitable[None]]] = []
        self._status_callbacks: list[Callable[[PipelineStatus], Awaitable[None]]] = []
        self._stop_event: asyncio.Event = asyncio.Event()

    @property
    def status(self) -> PipelineStatus:
        """Current pipeline status."""
        return self._status

    @property
    def sequence_number(self) -> int:
        """Current frame sequence number."""
        return self._sequence_number

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
            ret, _ = await loop.run_in_executor(None, cap.read)
            if not ret:
                return ConnectionError(
                    message="Video source opened but no frames could be read. The stream may not be active.",
                    url=url,
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

        self._release_capture()
        self._status = PipelineStatus.STOPPED

    def on_frame(self, callback: Callable[[np.ndarray, int], Awaitable[None]]) -> None:
        """Register a callback for each captured frame.

        The callback receives a BGR numpy array (1920×1080) and the frame
        sequence number.

        Args:
            callback: Async function accepting (frame: np.ndarray, seq: int).
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

    async def _notify_frame(self, frame: np.ndarray, seq: int) -> None:
        """Invoke all registered frame callbacks.

        Args:
            frame: The captured and resized BGR frame.
            seq: The frame sequence number.
        """
        for callback in self._frame_callbacks:
            try:
                await callback(frame, seq)
            except Exception as e:
                logger.error(f"Error in frame callback: {e}")

    async def _capture_loop(self) -> None:
        """Main async capture loop.

        Opens the video source, captures frames at the configured interval,
        resizes them to target resolution, and invokes callbacks. Handles
        disconnection with exponential backoff reconnection.
        """
        assert self._config is not None

        await self._set_status(PipelineStatus.RUNNING)

        loop = asyncio.get_event_loop()
        backoff_s = self._INITIAL_BACKOFF_S

        try:
            # Initial connection
            self._capture = await loop.run_in_executor(
                None, cv2.VideoCapture, self._config.source_url
            )

            if not self._capture.isOpened():
                logger.error(f"Failed to open video source: {self._config.source_url}")
                await self._set_status(PipelineStatus.DISCONNECTED)
                return

            # Main frame capture loop
            while not self._stop_event.is_set():
                ret, frame = await loop.run_in_executor(None, self._capture.read)

                if not ret or frame is None or frame.size == 0:
                    # Source disconnected — attempt reconnection
                    success = await self._reconnect_with_backoff()
                    if not success:
                        # All reconnection attempts exhausted
                        await self._set_status(PipelineStatus.DISCONNECTED)
                        return
                    # Reset backoff on successful reconnection
                    backoff_s = self._INITIAL_BACKOFF_S
                    continue

                # Reset backoff on each successful frame read
                backoff_s = self._INITIAL_BACKOFF_S

                # Resize frame to target resolution
                resized = self.resize_frame(
                    frame, self._config.target_width, self._config.target_height
                )

                # Increment sequence and notify
                self._sequence_number += 1
                await self._notify_frame(resized, self._sequence_number)

                # Wait for the configured interval before next capture
                interval_s = self._config.interval_ms / 1000.0
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=interval_s
                    )
                    # If we get here, stop was requested
                    break
                except asyncio.TimeoutError:
                    # Normal timeout — continue to next frame
                    pass

        except asyncio.CancelledError:
            logger.info("Capture loop cancelled.")
        except Exception as e:
            logger.error(f"Unexpected error in capture loop: {e}")
            await self._set_status(PipelineStatus.DISCONNECTED)
        finally:
            self._release_capture()
            if self._status != PipelineStatus.STOPPED:
                await self._set_status(PipelineStatus.STOPPED)

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
                    ret, _ = await loop.run_in_executor(None, self._capture.read)
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
        """Release the OpenCV VideoCapture resource if held."""
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception as e:
                logger.warning(f"Error releasing video capture: {e}")
            finally:
                self._capture = None
