"""The lazy public API of ``src.pipeline`` (PEP 562 ``__getattr__``).

Importing a submodule such as ``src.pipeline.state`` runs the package
``__init__`` first; it must not pull in the orchestrator (and through it torch
and EasyOCR). The names in ``__all__`` load on first access.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent


def run_python(code: str) -> str:
    """Run ``code`` in a fresh interpreter from the backend dir and return its stdout."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_importing_state_does_not_load_orchestrator_torch_or_easyocr():
    code = (
        "import sys, src.pipeline.state; "
        "print(all(m not in sys.modules for m in "
        "('torch', 'easyocr', 'src.pipeline.orchestrator')))"
    )
    assert run_python(code) == "True"


def test_public_names_resolve_to_their_classes():
    from src.pipeline import PipelineOrchestrator, PipelineState
    from src.pipeline.orchestrator import PipelineOrchestrator as Orchestrator
    from src.pipeline.state import PipelineState as State

    assert PipelineOrchestrator is Orchestrator
    assert PipelineState is State


def test_all_lists_exactly_the_lazy_public_names():
    import src.pipeline as pipeline

    assert sorted(pipeline.__all__) == ["PipelineOrchestrator", "PipelineState"]
    for name in pipeline.__all__:
        assert getattr(pipeline, name) is not None


def test_unknown_attribute_raises_attribute_error():
    import src.pipeline as pipeline

    with pytest.raises(AttributeError, match="no attribute 'does_not_exist'"):
        pipeline.does_not_exist  # noqa: B018


def test_star_import_yields_only_the_public_names():
    code = (
        "from src.pipeline import *; "
        "print(sorted(n for n in dir() if n in ('PipelineOrchestrator', 'PipelineState')))"
    )
    assert run_python(code) == "['PipelineOrchestrator', 'PipelineState']"
