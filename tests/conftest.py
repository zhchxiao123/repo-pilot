"""Shared test harness.

`fixture_repo` locates small repositories checked in under
``tests/fixtures/repos/`` that drive the pipeline's end-to-end tests.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE_REPOS_DIR = Path(__file__).parent / "fixtures" / "repos"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def git_origin(tmp_path):
    """A local git repo with two commits. Returns (path, first_sha, second_sha)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t.t")
    _git(origin, "config", "user.name", "t")
    (origin / "VERSION").write_text("one\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "first")
    first = _git(origin, "rev-parse", "HEAD")
    (origin / "VERSION").write_text("two\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "second")
    second = _git(origin, "rev-parse", "HEAD")
    return origin, first, second


@pytest.fixture
def git_repo_from(tmp_path):
    """Init a git repo populated from a source directory. Returns (path, commit)."""

    def _make(src: Path) -> tuple[Path, str]:
        origin = tmp_path / "src-origin"
        shutil.copytree(src, origin)
        _git(origin, "init", "-q", "-b", "main")
        _git(origin, "config", "user.email", "t@t.t")
        _git(origin, "config", "user.name", "t")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "import")
        return origin, _git(origin, "rev-parse", "HEAD")

    return _make


@pytest.fixture
def fixture_repo():
    def _load(name: str) -> Path:
        path = FIXTURE_REPOS_DIR / name
        if not path.is_dir():
            raise FileNotFoundError(f"fixture repo not found: {name} ({path})")
        return path

    return _load
