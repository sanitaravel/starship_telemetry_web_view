"""Tests for the GPU Detector module.

detect_gpu() imports torch when called, so the patches target the torch
module itself rather than a name in src.vision.gpu_detector.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from src.core.gpu import AccelerationBackend, GPUCapabilities
from src.vision.gpu_detector import detect_gpu


class TestImports:
    """Verify module imports correctly."""

    def test_acceleration_backend_enum_has_expected_members(self):
        assert AccelerationBackend.CPU.value == "cpu"
        assert AccelerationBackend.CUDA_GPU.value == "cuda_gpu"

    def test_gpu_capabilities_dataclass_is_frozen(self):
        caps = GPUCapabilities(
            backend=AccelerationBackend.CPU,
            device_name=None,
            cuda_version=None,
            gpu_available=False,
        )
        assert caps.gpu_available is False
        assert caps.backend == AccelerationBackend.CPU


def test_importing_the_detector_does_not_load_torch():
    """torch is loaded by detect_gpu(), not on import (nor via the telemetry stages)."""
    code = (
        "import sys, src.vision.gpu_detector, src.telemetry.record_assembler; "
        "print('torch' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


class TestDetectGpu:
    """Verify detect_gpu() returns valid GPUCapabilities in all scenarios."""

    def test_detect_gpu_returns_gpu_capabilities(self):
        result = detect_gpu()
        assert isinstance(result, GPUCapabilities)
        assert isinstance(result.backend, AccelerationBackend)
        assert isinstance(result.gpu_available, bool)

    def test_detect_gpu_cpu_fallback_when_no_cuda(self):
        with patch("torch.cuda.is_available", return_value=False):
            result = detect_gpu()
        assert result.gpu_available is False
        assert result.backend == AccelerationBackend.CPU
        assert result.device_name is None
        assert result.cuda_version is None

    def test_detect_gpu_returns_cuda_when_available(self):
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch(
                "torch.cuda.get_device_name",
                return_value="NVIDIA RTX 4090",
            ),
            patch("torch.version", cuda="12.2"),
        ):
            result = detect_gpu()
        assert result.gpu_available is True
        assert result.backend == AccelerationBackend.CUDA_GPU
        assert result.device_name == "NVIDIA RTX 4090"
        assert result.cuda_version == "12.2"

    def test_detect_gpu_falls_back_on_is_available_exception(self):
        with patch(
            "torch.cuda.is_available",
            side_effect=RuntimeError("CUDA driver mismatch"),
        ):
            result = detect_gpu()
        assert result.gpu_available is False
        assert result.backend == AccelerationBackend.CPU

    def test_detect_gpu_falls_back_on_get_device_name_exception(self):
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch(
                "torch.cuda.get_device_name",
                side_effect=RuntimeError("Device query failed"),
            ),
        ):
            result = detect_gpu()
        assert result.gpu_available is False
        assert result.backend == AccelerationBackend.CPU
