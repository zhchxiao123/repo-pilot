"""Read-only, repo-confined exploration tools for the plan agent (ADR-0016).

The LLM plan agent uses these to explore an unfamiliar repo the way a human would
— list, read, grep, find — but they are strictly read-only and confined to the
cloned repo (untrusted code: no host access, no traversal, no execution). Execution
is exclusively the sandbox's job.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".mypy_cache"}
_MAX_READ = 64_000
_MAX_HITS = 100
_MAX_FIND = 200

# files worth showing in the orientation seed (the agent reads the rest on demand)
_SEED_FILES = ("README.md", "Dockerfile", "docker-compose.yml", "compose.yaml", "Procfile", "Makefile")
_SEED_MAX_FILES = 150
_SEED_SNIPPET = 800


def seed_context(repo_dir: str | Path) -> str:
    """A light orientation dossier for the plan agent: file tree + a few key files.

    Deliberately small — the agent pulls whatever else it needs via the tools.
    """
    tools = RepoTools(repo_dir)
    listing = []
    for p in tools._walk_files():
        listing.append(str(p.relative_to(tools.root)))
        if len(listing) >= _SEED_MAX_FILES:
            break
    snippets = []
    for name in _SEED_FILES:
        content = tools.read_file(name)
        if not content.startswith("(file not found"):
            snippets.append(f"--- {name} ---\n{content[:_SEED_SNIPPET]}")
    return "Files:\n" + "\n".join(listing) + "\n\n" + "\n\n".join(snippets)


class RepoTools:
    """Read-only filesystem tools scoped to a single repository directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"path escapes the repository: {path}")
        return target

    def _walk_files(self):
        for p in self.root.rglob("*"):
            rel = p.relative_to(self.root)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            # skip symlinks: an untrusted repo could point one at a host file, and
            # search/find/seed read file contents without going through _resolve.
            if p.is_symlink():
                continue
            if p.is_file():
                yield p

    def list_dir(self, path: str = ".") -> list[str]:
        base = self._resolve(path)
        if not base.is_dir():
            return []
        entries = []
        for child in sorted(base.iterdir()):
            if child.name in _SKIP_DIRS:
                continue
            entries.append(child.name + "/" if child.is_dir() else child.name)
        return entries

    def read_file(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            return f"(file not found: {path})"
        try:
            text = target.read_text(errors="replace")
        except (OSError, UnicodeError):
            return f"(unable to read {path})"
        if len(text) > _MAX_READ:
            return text[:_MAX_READ] + f"\n(... truncated at {_MAX_READ} chars)"
        return text

    def search(self, pattern: str) -> list[str]:
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = re.compile(re.escape(pattern))
        hits: list[str] = []
        for p in self._walk_files():
            try:
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if regex.search(line):
                        hits.append(f"{p.relative_to(self.root)}:{i}: {line.strip()[:200]}")
                        if len(hits) >= _MAX_HITS:
                            return hits
            except (OSError, UnicodeError):
                continue
        return hits

    def find(self, glob: str) -> list[str]:
        results: list[str] = []
        for p in self._walk_files():
            rel = str(p.relative_to(self.root))
            if fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(p.name, glob):
                results.append(rel)
                if len(results) >= _MAX_FIND:
                    break
        return sorted(results)
