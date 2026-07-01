"""Shared test harness.

`fixture_repo` locates small repositories checked in under
``tests/fixtures/repos/`` that drive the pipeline's end-to-end tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_REPOS_DIR = Path(__file__).parent / "fixtures" / "repos"


@pytest.fixture
def fixture_repo():
    def _load(name: str) -> Path:
        path = FIXTURE_REPOS_DIR / name
        if not path.is_dir():
            raise FileNotFoundError(f"fixture repo not found: {name} ({path})")
        return path

    return _load
