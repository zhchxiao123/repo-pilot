"""LLM-assisted planning (Tier-B, ADR-0004/0005).

When deterministic planning yields no candidate — i.e. rules don't cover the stack
(non-Node, monorepo, unconventional setup) — the LLM reads the profile, evidence,
and actual repo files and *proposes* full Runbook candidates for any stack. Output
is schema-constrained; every proposal is still verified by the sandbox before it is
trusted (the LLM proposes, the sandbox adjudicates).
"""

from __future__ import annotations

import json
from pathlib import Path

from repo_pilot.confidence import confidence
from repo_pilot.model_client import ModelClient
from repo_pilot.planner import default_healthcheck
from repo_pilot.schemas import SchemaValidationError, validate_runbook

_LLM_EVIDENCE_ID = "ev_llm1"

# key files whose contents most reveal how to run a project
_KEY_FILES = (
    "README.md", "package.json", "pyproject.toml", "requirements.txt", "Pipfile",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "Makefile", "Dockerfile",
    "docker-compose.yml", "compose.yaml", "setup.py", "manage.py",
)
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
_MAX_FILES = 200
_SNIPPET_CHARS = 1500

_PROMPT = """You are a build-and-run expert. Given a repository's profile, extracted
evidence, and files, output ONLY a JSON array of 1-3 candidate ways to run this
project locally inside a single container. Each candidate is an object:

  {{"image": "<docker image matching the language, e.g. python:3.11-bookworm>",
    "setup": ["<install/build commands, in order>"],
    "start": "<command that starts the server in the FOREGROUND>",
    "port": <the port the server listens on, integer>}}

Rules:
- Base the plan on the project's real signals; do not invent files.
- `start` must run in the foreground (not backgrounded, no `&`).
- Output JSON only, no prose.

## Profile
{profile}

## Evidence
{evidence}

## Files
{context}
"""


def gather_context(repo_dir: str | Path) -> str:
    """A compact view of the repo for the model: file listing + key-file snippets."""
    repo_dir = Path(repo_dir)
    listing: list[str] = []
    for path in sorted(repo_dir.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.relative_to(repo_dir).parts):
            continue
        if path.is_file():
            listing.append(str(path.relative_to(repo_dir)))
        if len(listing) >= _MAX_FILES:
            break

    snippets = []
    for name in _KEY_FILES:
        f = repo_dir / name
        if f.is_file():
            snippets.append(f"--- {name} ---\n{f.read_text(errors='replace')[:_SNIPPET_CHARS]}")

    return "Tree:\n" + "\n".join(listing) + "\n\n" + "\n\n".join(snippets)


def _to_runbook(candidate: dict, repo: dict, index: int) -> dict:
    setup = [{"command": c} for c in candidate.get("setup", []) if isinstance(c, str)]
    start = str(candidate["start"])
    port = int(candidate.get("port", 8000))
    return {
        "schema_version": "v1",
        "id": f"llm_{index}",
        "status": "candidate",
        "confidence": confidence(["llm_inference"]),
        "evidence_refs": [_LLM_EVIDENCE_ID],
        "repo": repo,
        "runtime": {
            "image": str(candidate["image"]),
            "workdir": "/workspace/repo",
            "resources": {"cpu": 2, "memory": "4g", "pids": 512, "timeout_seconds": 900},
        },
        "steps": {"setup": setup, "start": [{"command": start, "expected_ports": [port]}]},
        "healthcheck": default_healthcheck(),
    }


def propose_runbooks(
    profile: dict, evidence: list[dict], context: str, client: ModelClient
) -> tuple[list[dict], list[dict]]:
    """Return (candidate runbooks, evidence items). Invalid proposals are dropped."""
    prompt = _PROMPT.format(
        profile=json.dumps(
            {k: profile.get(k) for k in ("languages", "frameworks", "package_managers")}
        ),
        evidence=json.dumps([{"kind": e["kind"], "excerpt": e["excerpt"]} for e in evidence]),
        context=context,
    )
    try:
        parsed = json.loads(client.complete(prompt))
    except json.JSONDecodeError:
        return [], []
    if not isinstance(parsed, list):
        return [], []

    candidates = []
    for i, item in enumerate(parsed):
        if not (isinstance(item, dict) and item.get("image") and item.get("start")):
            continue
        runbook = _to_runbook(item, profile["repo"], i)
        try:
            validate_runbook(runbook)
        except SchemaValidationError:
            continue
        candidates.append(runbook)

    if not candidates:
        return [], []

    ev = {
        "id": _LLM_EVIDENCE_ID,
        "file": "(llm)",
        "line": None,
        "kind": "llm_inference",
        "excerpt": "LLM-proposed run plan from profile + evidence + files",
        "reason": "deterministic planning produced no candidate for this stack",
        "confidence": 0.3,
    }
    return candidates, [ev]
