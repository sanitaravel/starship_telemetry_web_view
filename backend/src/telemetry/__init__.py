"""Turning one frame's OCR and engine results into a telemetry record.

- ``stage_assignment``: ``StageAssigner``, which attributes values to Starship or Super Heavy
  by separation state (stateful, so it runs in video order).
- ``record_assembler``: builds the ``TelemetryRecord`` from the stage results.

Nothing is imported here, so importing a leaf module stays cheap.
"""
