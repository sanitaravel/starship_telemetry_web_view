"""Telemetry extraction pipeline.

Frame Extractor → Engine Analyzer → OCR → Stage Assignment → Record Assembly → WebSocket broadcast.

- ``orchestrator``: ``PipelineOrchestrator``, lifecycle, frame dispatch and wiring.
- ``frame_processor``: the per-frame work (stages, record assembly) and its resources.
- ``stages``: stateless stage helpers (timed executor runs, preview encoding, stage fallbacks).
- ``ordering``: the reorder buffer coordination that broadcasts results in video order.
- ``broadcast``: the ordered broadcast queue and its worker task.
- ``state`` and ``status_payload``: ``PipelineState`` and the status message built from it.
- ``reorder_buffer``, ``concurrency_controller``, ``parallel_config``: the building blocks.
"""

from src.pipeline.orchestrator import PipelineOrchestrator
from src.pipeline.state import PipelineState

__all__ = ["PipelineOrchestrator", "PipelineState"]
