"""GPU Detector - probes for CUDA GPU availability at startup.

Detects whether a CUDA-capable GPU is present via PyTorch and returns
a GPUCapabilities descriptor used to configure EasyOCR acceleration.
Falls back gracefully to CPU mode on any detection failure.

torch is imported inside detect_gpu(), so importing this module stays cheap.
"""

from src.core.gpu import AccelerationBackend, GPUCapabilities


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
    import torch

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
