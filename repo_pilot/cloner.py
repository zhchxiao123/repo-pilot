"""Repo Cloner (§14.1).

Clones a repository into a job workspace and, if a commit is given, checks it out
exactly. Records the resolved commit and default branch as a :class:`RepoRef`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoRef:
    repo_dir: Path
    commit: str
    default_branch: str


class CloneError(RuntimeError):
    """Raised when a git operation during cloning fails."""


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise CloneError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


class RepoCloner:
    """Clones repositories, optionally pinning to a specific commit."""

    def clone(
        self, repo_url: str, commit: str | None = None, *, dest: str | Path
    ) -> RepoRef:
        # Resolve to an absolute path: the clone runs with cwd=dest.parent and later
        # git calls run with cwd=dest, so a relative dest (e.g. the default
        # "artifacts/..." root) would otherwise nest the clone and break follow-up
        # commands. An absolute repo_dir also keeps the compose build context valid.
        dest = Path(dest).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Shallow clone the default branch; deepen only if a specific commit is needed.
        _git(dest.parent, "clone", "--depth", "1", repo_url, str(dest))
        default_branch = _git(dest, "rev-parse", "--abbrev-ref", "HEAD")

        if commit is not None:
            # The shallow clone may not contain the requested commit; fetch it.
            try:
                _git(dest, "fetch", "--depth", "1", "origin", commit)
            except CloneError:
                _git(dest, "fetch", "--unshallow")
            _git(dest, "checkout", "-q", commit)

        resolved = _git(dest, "rev-parse", "HEAD")
        return RepoRef(repo_dir=dest, commit=resolved, default_branch=default_branch)
