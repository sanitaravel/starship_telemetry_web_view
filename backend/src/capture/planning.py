"""Planning and starting parallel decoding of a seekable source (see parallel_decoder)."""

import logging
import os

from src.capture.config import (
    _PARALLEL_CHUNK_SOURCE_FRAMES,
    _PARALLEL_MAX_BUFFERED,
    _PARALLEL_MIN_CHUNK_SOURCE_FRAMES,
)
from src.capture.parallel_decoder import ParallelFileDecoder


logger = logging.getLogger(__name__)


def parallel_plan(decode_workers: int, skip: int) -> tuple[int, int, int] | None:
    """Return (kept frames per chunk, decoders, FFmpeg threads per decoder).

    Uses as many decoders as the memory budget allows while each chunk
    still spans enough source to pay for its seek: more decoders mean
    shorter chunks, so at shorter skip intervals fewer of them run. None
    when parallel decoding is turned off or would not pay at this skip
    interval even with two decoders (see _PARALLEL_* in config).

    Args:
        decode_workers: The configured most decoders (FrameExtractorConfig).
        skip: The skip interval: every ``skip``-th frame is kept.
    """
    # More decoders than physical cores only fight over them
    most = min(decode_workers, (os.cpu_count() or 2) // 2)
    for workers in range(most, 1, -1):
        chunk = min(
            -(-_PARALLEL_CHUNK_SOURCE_FRAMES // skip),
            _PARALLEL_MAX_BUFFERED // (workers + 1),
        )
        if chunk * skip >= _PARALLEL_MIN_CHUNK_SOURCE_FRAMES:
            threads = max(1, (os.cpu_count() or 2) // workers)
            return chunk, workers, threads
    return None


def first_kept_frame(frame_counter: int, skip: int) -> int:
    """Index of the first frame at or after ``frame_counter`` the serial path keeps.

    Same frames the serial path would keep: those with (frame_number + 1) a
    multiple of skip.
    """
    return frame_counter + skip - 1 - frame_counter % skip


def start_decoder(
    source_url: str,
    frame_counter: int,
    skip: int,
    plan: tuple[int, int, int],
    frame_count: int,
) -> ParallelFileDecoder:
    """Start a parallel decoder from ``frame_counter`` following ``plan``.

    Args:
        source_url: The seekable source.
        frame_counter: Frames read so far; decoding resumes from the next kept one.
        skip: The skip interval.
        plan: (kept frames per chunk, decoders, threads per decoder), see parallel_plan.
        frame_count: The source's length, from source.probe_frame_count.
    """
    chunk, workers, threads = plan
    first = first_kept_frame(frame_counter, skip)
    decoder = ParallelFileDecoder(
        source_url,
        first_frame=first,
        skip=skip,
        workers=workers,
        chunk_frames=chunk,
        max_buffered=(workers + 1) * chunk,
        threads_per_decoder=threads,
        reported_frame_count=frame_count,
    )
    logger.info(
        "Decoding in parallel from frame %d: %d decoders x %d threads, "
        "%d kept frames per chunk.",
        first, workers, threads, chunk,
    )
    return decoder
