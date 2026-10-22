"""The backend packages only import from their own layer or the layers below it.

Every module under ``src/`` is parsed with ``ast`` (nothing is imported, so the
check is cheap and sees imports inside functions too) and each ``src.*`` import
is checked against ``LAYERS``. Packages that share a layer are peers and must
not import each other.
"""

import ast
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

# Lowest first. Packages in the same tuple are peers.
LAYERS: list[tuple[str, ...]] = [
    ("core",),
    ("templates",),
    ("vision", "ocr", "capture"),
    ("telemetry",),
    ("storage",),
    ("pipeline",),
    ("api",),
    ("server",),
]

RANK = {package: rank for rank, layer in enumerate(LAYERS) for package in layer}


def module_name(path: Path) -> str:
    """``src/ocr/engine.py`` -> ``src.ocr.engine``; a package's ``__init__`` is the package."""
    parts = list(path.relative_to(SRC_DIR.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def src_imports(source: str, module: str, is_package: bool) -> set[str]:
    """Every ``src.*`` module a source file imports, at any depth (functions included)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Resolve a relative import against the importing module's package.
                base = module.split(".")
                if not is_package:
                    base = base[:-1]
                base = base[: len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            found.add(target)
            # ``from src import missions`` / ``from src.storage import missions``
            # may name submodules; count them as imports of the submodule too.
            found.update(f"{target}.{alias.name}" for alias in node.names if alias.name != "*")
    return {name for name in found if name.split(".")[0] == "src" and name.count(".") >= 1}


def package_of(name: str) -> str:
    """``src.ocr.engine`` -> ``ocr``, ``src.server`` -> ``server``."""
    return name.split(".")[1]


def layer_violations(imports: dict[str, set[str]]) -> list[str]:
    """Readable messages for every import that reaches a peer or a higher layer."""
    problems = []
    for module, targets in sorted(imports.items()):
        source = package_of(module)
        for target in sorted(targets):
            dest = package_of(target)
            if target.rsplit(".", 1)[0] in targets and target.count(".") > 1:
                # ``from src.a.b import name``: ``src.a.b`` is in the same package
                # and gets the same verdict, so report the import once.
                continue
            if dest not in RANK:
                problems.append(f"{module} imports {target} ({dest} is not a package in LAYERS)")
                continue
            if dest == source or RANK[dest] < RANK[source]:
                continue
            relation = "a peer of" if RANK[dest] == RANK[source] else "above"
            problems.append(
                f"{module} imports {target} ({source} may not depend on {dest}, "
                f"which is {relation} it)"
            )
    return problems


def collect_imports() -> dict[str, set[str]]:
    imports = {}
    for path in sorted(SRC_DIR.rglob("*.py")):
        module = module_name(path)
        if module == "src":
            continue
        imports[module] = src_imports(
            path.read_text(encoding="utf-8"), module, path.name == "__init__.py"
        )
    return imports


def test_every_package_has_a_layer():
    """A new package under src/ must be placed in LAYERS before it can be checked."""
    packages = {
        path.name if path.is_dir() else path.stem
        for path in SRC_DIR.iterdir()
        if (path.is_dir() and (path / "__init__.py").exists())
        or (path.suffix == ".py" and path.stem != "__init__")
    }
    assert packages - RANK.keys() == set(), "add these packages to LAYERS"
    assert RANK.keys() - packages == set(), "LAYERS names packages that no longer exist"


def test_scan_sees_the_import_graph():
    """Guard against a vacuous pass: the scan must find known imports."""
    imports = collect_imports()
    assert "src.api.app" in imports["src.server"]
    assert "src.ocr.results" in imports["src.telemetry.stage_assignment"]
    assert "src.core.paths" in imports["src.storage.missions"]


def test_packages_only_import_same_or_lower_layers():
    problems = layer_violations(collect_imports())
    assert not problems, "layering violations:\n" + "\n".join(problems)


@pytest.mark.parametrize(
    ("module", "target", "expected"),
    [
        (
            "src.vision.x",
            "src.pipeline.y",
            "src.vision.x imports src.pipeline.y (vision may not depend on pipeline, which is above it)",
        ),
        (
            "src.ocr.engine",
            "src.vision.gpu_detector",
            "(ocr may not depend on vision, which is a peer of it)",
        ),
        ("src.core.enums", "src.templates.svg_parser", "(core may not depend on templates"),
        ("src.storage.x", "src.missing.y", "(missing is not a package in LAYERS)"),
    ],
)
def test_checker_reports_upward_and_peer_imports(module, target, expected):
    [problem] = layer_violations({module: {target}})
    assert expected in problem


def test_checker_allows_downward_and_internal_imports():
    assert layer_violations({
        "src.telemetry.record_assembler": {"src.ocr.results", "src.vision.engine_analyzer", "src.core.enums"},
        "src.pipeline.orchestrator": {"src.pipeline.ordering", "src.capture"},
    }) == []


def test_function_level_and_relative_imports_are_seen():
    source = (
        "def f():\n"
        "    from src.api import runtime\n"
        "from .sibling import thing\n"
        "from src import missions\n"
    )
    found = src_imports(source, "src.vision.x", is_package=False)
    assert {"src.api", "src.api.runtime", "src.vision.sibling", "src.missions"} <= found


def test_a_from_import_is_reported_once():
    imports = {"src.ocr.engine": {"src.vision.gpu_detector", "src.vision.gpu_detector.GPUCapabilities"}}
    assert layer_violations(imports) == [
        "src.ocr.engine imports src.vision.gpu_detector "
        "(ocr may not depend on vision, which is a peer of it)"
    ]
