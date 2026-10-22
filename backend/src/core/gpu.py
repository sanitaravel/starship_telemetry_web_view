"""Compute backend types shared by the OCR and vision stages.

``detect_gpu()`` in ``src.vision.gpu_detector`` produces a ``GPUCapabilities``;
the types live here so the stages that only consume it don't depend on vision
(or on torch).
"""

from dataclasses import dataclass
from enum import Enum


class AccelerationBackend(Enum):
    """Supported compute backends for inference."""

    CPU = "cpu"
    CUDA_GPU = "cuda_gpu"


@dataclass(frozen=True)
class GPUCapabilities:
    """Describes the detected GPU/compute capabilities of the host."""

    backend: AccelerationBackend
    device_name: str | None  # e.g., "NVIDIA RTX 4090"
    cuda_version: str | None  # e.g., "12.2"
    gpu_available: bool  # True if CUDA GPU detected
