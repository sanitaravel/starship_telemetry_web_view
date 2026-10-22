"""OpenCV access to a video source, independent of the extractor's state.

Every ``cv2.VideoCapture`` is constructed here, so tests patch
``src.capture.source.cv2.VideoCapture``.
"""

import asyncio
import logging
import threading

import cv2
import numpy as np

from src.capture.config import DEFAULT_SOURCE_FPS, SourceUnavailable


logger = logging.getLogger(__name__)


def open_capture(source_url: str) -> cv2.VideoCapture:
    """Open ``source_url`` with OpenCV (blocking; run it in the executor)."""
    return cv2.VideoCapture(source_url)


async def validate_source(url: str) -> None | SourceUnavailable:
    """Check whether the video source is reachable and active.

    Attempts to open the video source URL with OpenCV and read a single
    frame to verify the source is active and producing video.

    Args:
        url: Video source URL to validate.

    Returns:
        None if the source is reachable and active, or a SourceUnavailable
        with a descriptive message if not.
    """
    if not url or not url.strip():
        return SourceUnavailable(
            message="Video source URL cannot be empty.",
            url=url,
        )

    cap: cv2.VideoCapture | None = None
    try:
        # Run blocking OpenCV operations in a thread to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        cap = await loop.run_in_executor(None, open_capture, url)

        if not cap.isOpened():
            return SourceUnavailable(
                message=f"Unable to open video source. The URL may be unreachable or invalid.",
                url=url,
            )

        # Try reading a single frame to verify the stream is active
        ret, frame = await loop.run_in_executor(None, cap.read)
        if not ret:
            return SourceUnavailable(
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
        return SourceUnavailable(
            message=f"Error validating video source: {str(e)}",
            url=url,
        )
    finally:
        if cap is not None:
            cap.release()


def parse_source_fps(reported_fps: object) -> float:
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


def configure_capture(
    capture: cv2.VideoCapture, target_width: int, target_height: int
) -> float:
    """Request the target resolution from a freshly opened capture.

    Logs the source frame rate and whether the source honoured the request.

    Returns:
        The source frame rate (see parse_source_fps).
    """
    # Request 1080p from source (hint — not all backends honor this)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, target_width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, target_height)

    # Read the source frame rate. Some backends/streams report 0, NaN,
    # or an unparseable value; fall back to the NTSC default in that case.
    source_fps = parse_source_fps(capture.get(cv2.CAP_PROP_FPS))
    logger.info("Source frame rate: %.3f FPS.", source_fps)

    actual_w = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_h = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
    if actual_w != target_width or actual_h != target_height:
        logger.warning(
            f"Requested {target_width}x{target_height} "
            f"but source provides {int(actual_w)}x{int(actual_h)}. "
            f"Frames will be resized."
        )
    else:
        logger.info(
            f"Source confirmed at {int(actual_w)}x{int(actual_h)}."
        )
    return source_fps


def advance(
    capture: cv2.VideoCapture,
    to_skip: int,
    capture_lock: threading.Lock,
    stop_event: asyncio.Event,
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
        with capture_lock:
            if stop_event.is_set() or not capture.grab():
                return grabbed, False, None
        grabbed += 1
    with capture_lock:
        if stop_event.is_set():
            return grabbed, False, None
        ret, frame = capture.read()
    return grabbed, ret, frame


def probe_frame_count(source_url: str) -> int:
    """Return the source's frame count if it seeks accurately, else 0.

    Runs in the executor, on an FFmpeg-backed source. The check opens a
    capture of its own, leaving the extractor's one where it is.
    """
    probe = cv2.VideoCapture(source_url, cv2.CAP_FFMPEG)
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
