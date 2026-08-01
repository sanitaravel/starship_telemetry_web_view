"""GPU Detector - probes for CUDA GPU availability at startup.

Detects whether a CUDA-capable GPU is present via PyTorch and returns
a GPUCapabilities descriptor used to configure EasyOCR acceleration.
Falls back gracefully to CPU mode on any detection failure.
"""

from dataclasses import dataclass
from enum import Enum

import torch


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


_CPU_FALLBACK = GPUCapabilities(
    backend=AccelerationBackend.CPU,
    device_name=None,
    cuda_version=None,
    gpu_available=False,
)


def detect_gpu() -> GPUCapabilities:
    """Detect CUDA GPU availability using torch.cuda.is_available().

    Returns GPUCapabilities with gpu_available=True if a working CUDA GPU is
    present. If no GPU is found or if any exception occurs (e.g., CUDA driver
    version mismatch, RuntimeError from torch), returns a CPU fallback
    configuration so the system can continue without GPU acceleration.
    """
    try:
        gpu_available = torch.cuda.is_available()
    except Exception:
        # Driver mismatch, missing libraries, or other runtime errors
        return _CPU_FALLBACK

    if not gpu_available:
        return _CPU_FALLBACK

    try:
        device_name = torch.cuda.get_device_name(0)
        cuda_version = torch.version.cuda
    except Exception:
        # GPU reported available but querying details failed
        return _CPU_FALLBACK

    return GPUCapabilities(
        backend=AccelerationBackend.CUDA_GPU,
        device_name=device_name,
        cuda_version=cuda_version,
        gpu_available=True,
    )
