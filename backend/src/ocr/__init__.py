"""OCR of the telemetry overlay's text regions.

- ``engine``: ``EasyOCREngine`` (T-0 gate, per-frame recognition) and ``configure_torch_threads``.
- ``results``: ``OCRFieldResult`` and ``OCRResult``, the types the later stages consume.
- ``parsing``: field kinds, allowlists and the parsers for time, numbers and units.
- ``regions``: ROI clamping and cropping, grayscale and the text-ink helpers.
- ``cache``: ``FieldCache``, which reuses static ROIs' results while their pixels are unchanged.
- ``batcher``: ``BatchedRecognizer``, one recogniser pass over every frame in flight.
- ``mixed``: that pass on GPU, each crop under its own allowlist (EasyOCR internals).

Only the result types are re-exported: importing the package must stay cheap,
so the engine (torch, EasyOCR) is imported from ``src.ocr.engine`` explicitly.
"""

from src.ocr.results import OCRFieldResult, OCRResult

__all__ = ["OCRFieldResult", "OCRResult"]
