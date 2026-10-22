"""Frame capture configuration, tuning constants and the validation result."""

from dataclasses import dataclass


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
class SourceUnavailable:
    """Why a video source failed validation: unreachable, unreadable or empty.

    Returned (not raised) by ``FrameExtractor.validate``, which returns None
    for a usable source.

    Attributes:
        message: Human-readable error description.
        url: The URL that was attempted.
    """

    message: str
    url: str
