"""Telemetry extraction pipeline.

Frame Extractor → Engine Analyzer → OCR → Stage Assignment → Record Assembly → WebSocket broadcast.

- ``orchestrator``: ``PipelineOrchestrator``, lifecycle, frame dispatch and wiring.
- ``lifecycle``: resolving the ROI template and cancelling in-flight tasks on stop.
- ``frame_processor``: the per-frame work (stages, record assembly) and its resources.
- ``stages``: stateless stage helpers (timed executor runs, preview encoding, stage fallbacks).
- ``ordering``: the reorder buffer coordination that broadcasts results in video order.
- ``broadcast``: the ordered broadcast queue and its worker task.
- ``state`` and ``status_payload``: ``PipelineState`` and the status message built from it.
- ``reorder_buffer``, ``concurrency_controller``, ``parallel_config``: the building blocks.

The public names load on first access (PEP 562), so importing a submodule such
as ``src.pipeline.state`` does not pull in the orchestrator, torch or EasyOCR.
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.pipeline.orchestrator import PipelineOrchestrator
    from src.pipeline.state import PipelineState

__all__ = ["PipelineOrchestrator", "PipelineState"]

_EXPORTS = {
    "PipelineOrchestrator": "src.pipeline.orchestrator",
    "PipelineState": "src.pipeline.state",
}


def __getattr__(name: str) -> object:
    if name in _EXPORTS:
        return getattr(importlib.import_module(_EXPORTS[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
