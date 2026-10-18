"""Application version: MAJOR.MINOR.PATCH from the repo's VERSION file, plus a build number.

The build number is the commit count of the checked-out branch
(``git rev-list --count HEAD``), so every commit gets a distinct, increasing
fourth number, e.g. ``0.1.0.64``. Without git (e.g. an exported copy) only
the three-part release version is reported. The frontend applies the same
rule at build time (frontend/vite.config.ts).
"""

import re
import subprocess
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"

SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def read_release_version(path: Path = VERSION_FILE) -> str:
    """Return the MAJOR.MINOR.PATCH version from the VERSION file."""
    version = path.read_text(encoding="utf-8").strip()
    if not SEMVER_PATTERN.match(version):
        raise ValueError(f"{path} must hold MAJOR.MINOR.PATCH, got {version!r}")
    return version


def git_commit_count(repo: Path = REPO_ROOT) -> int | None:
    """Commit count of HEAD, or None when git or the repository is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return int(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def compose_version(release: str, build: int | None) -> str:
    """Join the release version and build number: '0.1.0' + 64 -> '0.1.0.64'."""
    return release if build is None else f"{release}.{build}"


@lru_cache(maxsize=1)
def get_version() -> str:
    """The full application version, computed once per process."""
    return compose_version(read_release_version(), git_commit_count())
