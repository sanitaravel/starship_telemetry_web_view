"""Pipeline status and the frame/status callbacks of the frame extractor."""

import logging
from typing import Awaitable, Callable

import numpy as np

from src.enums import PipelineStatus


logger = logging.getLogger(__name__)


class CaptureNotifier:
    """Holds the pipeline status and notifies the registered callbacks.

    The base of FrameExtractor: its outward-facing events, kept apart from
    how frames are captured.
    """

    def __init__(self) -> None:
        self._status: PipelineStatus = PipelineStatus.STOPPED
        self._frame_callbacks: list[
            Callable[[np.ndarray, int, int, float], Awaitable[None]]
        ] = []
        self._status_callbacks: list[Callable[[PipelineStatus], Awaitable[None]]] = []

    @property
    def status(self) -> PipelineStatus:
        """Current pipeline status."""
        return self._status

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
