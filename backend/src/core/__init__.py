"""Shared foundations every other layer may import.

- ``enums``: ``PipelineStatus``, ``SeparationState``, ``OCRFieldStatus`` and the other shared enums.
- ``models``: the ROI dataclasses produced by SVG parsing, and ``ParseError``.
- ``gpu``: ``AccelerationBackend`` and ``GPUCapabilities``, the compute backend types.
- ``telemetry_record``: ``TelemetryRecord``, the broadcast record model.
- ``version``: the application version (VERSION file plus the commit count).
- ``logging_config``: ``configure_logging()``, uvicorn-style log formatting.
- ``logging_context``: the contextvar carrying the frame sequence number.
- ``paths``: ``REPO_ROOT``, the repository root the data directories hang off.

Nothing is imported here, so importing a leaf module stays cheap.
"""
