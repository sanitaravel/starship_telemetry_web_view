"""Builds the pipeline status message sent over the API and WebSocket."""

from typing import Any

from src.core.gpu import GPUCapabilities
from src.core.version import get_version
from src.pipeline.state import PipelineState


def build_status_payload(
    state: PipelineState,
    gpu_capabilities: GPUCapabilities,
    processing_fps: float,
    in_flight_frames: int,
) -> dict[str, Any]:
    """Build the pipeline status payload dictionary.

    Args:
        state: The current pipeline state.
        gpu_capabilities: Detected GPU capabilities.
        processing_fps: Current processing FPS from the FPS meter.
        in_flight_frames: Frames currently being processed concurrently.

    Returns:
        Dictionary with status, gpu info, skip_frames,
        current_sequence, processing_fps, in_flight_frames,
        parallel_mode_active, frames_discarded and the backend
        version for API and WebSocket responses.
    """
    return {
        "status": state.status.value,
        "version": get_version(),
        "gpu": {
            "available": gpu_capabilities.gpu_available,
            "device_name": gpu_capabilities.device_name,
        },
        "skip_frames": state.skip_frames,
        "current_sequence": state.current_sequence,
        "processing_fps": processing_fps,
        "in_flight_frames": in_flight_frames,
        "parallel_mode_active": state.parallel_mode_active,
        "frames_discarded": state.frames_discarded,
    }
