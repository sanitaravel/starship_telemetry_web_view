"""Bump the application version (MAJOR.MINOR.PATCH) everywhere it is recorded.

VERSION at the repo root is the source of truth; frontend/package.json and
backend/pyproject.toml carry copies for their package tooling. The fourth,
build number is the git commit count and is never written down.

Usage:
    python scripts/bump_version.py patch      # 0.1.0 -> 0.1.1  (fixes)
    python scripts/bump_version.py minor      # 0.1.0 -> 0.2.0  (features)
    python scripts/bump_version.py major      # 0.1.0 -> 1.0.0  (breaking / stable)
    python scripts/bump_version.py 0.3.0      # set explicitly
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
PACKAGE_JSON = ROOT / "frontend" / "package.json"
PYPROJECT = ROOT / "backend" / "pyproject.toml"

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def next_version(current: str, part: str) -> str:
    """Return the version after bumping ``part``, or ``part`` itself if it is a version."""
    if SEMVER.match(part):
        return part
    major, minor, patch = (int(n) for n in SEMVER.match(current).groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"expected major, minor, patch or X.Y.Z, got {part!r}")


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    """Replace the single match of ``pattern`` in ``path``, keeping everything else."""
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"version field not found in {path}")
    path.write_text(updated, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    current = VERSION_FILE.read_text(encoding="utf-8").strip()
    new = next_version(current, sys.argv[1])

    VERSION_FILE.write_text(new + "\n", encoding="utf-8")
    replace_once(PACKAGE_JSON, r'^(  "version": )"[^"]*"', rf'\g<1>"{new}"')
    replace_once(PYPROJECT, r'^version = "[^"]*"', f'version = "{new}"')
    print(f"{current} -> {new}")


if __name__ == "__main__":
    main()
