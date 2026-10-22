"""Frame capture from a video source.

- ``extractor``: ``FrameExtractor``, lifecycle, status and frame callbacks, and the capture loop.
- ``config``: ``FrameExtractorConfig``, the tuning constants and ``SourceUnavailable``.
- ``source``: OpenCV access (open, validate, frame rate and resolution, grab/read, seek check).
- ``planning``: how many parallel decoders a skip interval pays for, and where they start.
- ``reconnect``: retrying a lost source with exponential backoff.
- ``parallel_decoder``: ``ParallelFileDecoder``, several decoders over a seekable file.
"""

from src.capture.config import FrameExtractorConfig, SourceUnavailable
from src.capture.extractor import FrameExtractor

__all__ = ["FrameExtractor", "FrameExtractorConfig", "SourceUnavailable"]
