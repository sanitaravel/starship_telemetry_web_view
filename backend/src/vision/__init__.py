"""Computer vision on the captured frames, apart from OCR.

- ``engine_analyzer``: engine status from Hough circles and colour classification of the ROIs.
- ``gpu_detector``: ``detect_gpu()``, whether OCR runs on CUDA or CPU (torch is imported on call).

Nothing is imported here, so importing a leaf module stays cheap.
"""
