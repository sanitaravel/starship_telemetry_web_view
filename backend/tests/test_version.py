"""Tests for the application version (src/version.py) and the bump script."""

import importlib.util
import json
import re
import tomllib

import pytest

from src.version import (
    REPO_ROOT,
    compose_version,
    get_version,
    git_commit_count,
    read_release_version,
)


def _load_bump_script():
    spec = importlib.util.spec_from_file_location(
        "bump_version", REPO_ROOT / "scripts" / "bump_version.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_copies_match_the_version_file():
    release = read_release_version()
    package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    assert package["version"] == release, "run scripts/bump_version.py instead of editing by hand"
    assert pyproject["project"]["version"] == release


def test_full_version_is_release_plus_commit_count():
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.\d+)?", get_version())
    count = git_commit_count()
    if count is not None:
        assert get_version() == f"{read_release_version()}.{count}"


def test_compose_version():
    assert compose_version("0.1.0", 64) == "0.1.0.64"
    assert compose_version("0.1.0", None) == "0.1.0"


def test_release_version_must_be_semver(tmp_path):
    bad = tmp_path / "VERSION"
    bad.write_text("0.1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        read_release_version(bad)


def test_commit_count_is_none_outside_a_repository(tmp_path):
    assert git_commit_count(tmp_path) is None


@pytest.mark.parametrize(
    ("part", "expected"),
    [("patch", "0.4.3"), ("minor", "0.5.0"), ("major", "1.0.0"), ("2.0.1", "2.0.1")],
)
def test_bump_script_next_version(part, expected):
    assert _load_bump_script().next_version("0.4.2", part) == expected


def test_bump_script_rejects_unknown_part():
    with pytest.raises(ValueError):
        _load_bump_script().next_version("0.4.2", "huge")
