"""PipelineState - the pipeline's status, settings and counters."""

from dataclasses import dataclass

from src.core.enums import PipelineStatus, SeparationState
from src.vision.gpu_detector import GPUCapabilities


@dataclass
class PipelineState:
    """Tracks the current state of the extraction pipeline."""

    status: PipelineStatus = PipelineStatus.STOPPED
    source_url: str | None = None
    source_validated: bool = False
    skip_frames: int = 30
    current_sequence: int = 0
    separation_state: SeparationState = SeparationState.PRE_SEPARATION
    active_template_name: str = "starship_rois"
    gpu_capabilities: GPUCapabilities | None = None
    processing_fps: float = 0.0  # actual frames processed per second
    in_flight_frames: int = 0  # Current concurrent frame count
    t_zero_detected: bool = False  # Whether T-0 has been detected
    parallel_mode_active: bool = False  # Whether inter-frame parallelism is enabled
    frames_discarded: int = 0  # Count of frames discarded due to capacity
    buffer_size: int = 0  # Current reorder buffer occupancy
