"""Stateless helpers for the per-frame stages.

Running a function in a thread pool with timing, encoding the frame preview,
and falling back to a default result when a stage fails.
"""

import asyncio
import base64
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import cv2
import numpy as np

from src.engine_analyzer import EngineAnalysisResult
from src.ocr.results import OCRResult


logger = logging.getLogger(__name__)

# Frame previews are encoded at this size: the dashboard shows a thumbnail,
# and a quarter of the pixels makes encoding, base64 and JSON ~4x cheaper.
_PREVIEW_SIZE = (960, 540)
_PREVIEW_JPEG_QUALITY = 60


async def run_timed(
    executor: ThreadPoolExecutor | None, fn: Callable[..., Any], *args: Any
) -> tuple[Any, int, int]:
    """Run fn(*args) in the executor and time it from inside the worker.

    Returns:
        (result, compute time in ms, time spent waiting for a worker in ms).
        Timing inside the job keeps queue wait out of the stage duration.
    """
    loop = asyncio.get_running_loop()
    submitted = time.monotonic()

    def job() -> tuple[Any, float, float]:
        started = time.monotonic()
        return fn(*args), started, time.monotonic()

    result, started, finished = await loop.run_in_executor(executor, job)
    return result, int((finished - started) * 1000), int((started - submitted) * 1000)


def encode_preview(frame: np.ndarray) -> str:
    """Downscale the frame and encode it as a base64 JPEG string.

    Args:
        frame: BGR numpy array (1920x1080).

    Returns:
        Base64-encoded JPEG string.
    """
    preview = cv2.resize(frame, _PREVIEW_SIZE, interpolation=cv2.INTER_AREA)
    _, jpeg_buf = cv2.imencode(
        '.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, _PREVIEW_JPEG_QUALITY]
    )
    return base64.b64encode(jpeg_buf.tobytes()).decode('ascii')


def unpack_stage_results(
    seq: int,
    engine_result_or_exc: EngineAnalysisResult | BaseException,
    ocr_result_or_exc: OCRResult | BaseException,
) -> tuple[EngineAnalysisResult, OCRResult] | None:
    """Replace a failed stage's exception with its default result.

    Logs each failed stage. When only one stage failed, the other's result
    is kept and the failed one falls back to an empty/UNAVAILABLE result.

    Args:
        seq: Frame sequence number (for logging).
        engine_result_or_exc: Engine analysis result or the exception it raised.
        ocr_result_or_exc: OCR extraction result or the exception it raised.

    Returns:
        (engine_result, ocr_result), or None when both stages failed and
        the frame should be skipped.
    """
    if isinstance(engine_result_or_exc, BaseException):
        logger.error(
            "Engine analysis failed for frame %d: %s",
            seq,
            engine_result_or_exc,
            exc_info=engine_result_or_exc,
        )
        engine_result = EngineAnalysisResult.empty()
    else:
        engine_result = engine_result_or_exc

    if isinstance(ocr_result_or_exc, BaseException):
        logger.error(
            "OCR extraction failed for frame %d: %s",
            seq,
            ocr_result_or_exc,
            exc_info=ocr_result_or_exc,
        )
        ocr_result = OCRResult.unavailable()
    else:
        ocr_result = ocr_result_or_exc

    if isinstance(engine_result_or_exc, BaseException) and isinstance(
        ocr_result_or_exc, BaseException
    ):
        logger.error(
            "Both engine analysis and OCR failed for frame %d, skipping frame.",
            seq,
        )
        return None
    return engine_result, ocr_result
