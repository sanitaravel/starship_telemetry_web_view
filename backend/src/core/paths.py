"""Repository locations shared by the modules that read files outside ``backend/``."""

from pathlib import Path

# This file is backend/src/core/paths.py, so parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
