"""Signal extractors (§6).

Deterministic scanners that emit Evidence for run signals beyond the package
manifest: README command blocks, GitHub Actions run steps, and the presence of a
Dockerfile or compose file. No LLM.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from repo_pilot.evidence import EvidenceBuilder

_FENCED = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_COMMAND_TOOLS = (
    "npm", "pnpm", "yarn", "node", "python", "pip", "uv", "go", "cargo",
    "mvn", "gradle", "make", "docker", "flask", "uvicorn", "django-admin",
)

_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def _looks_like_command(line: str) -> bool:
    return line.split(" ", 1)[0] in _COMMAND_TOOLS if line else False


def _extract_readme(repo_dir: Path, builder: EvidenceBuilder) -> None:
    readme = repo_dir / "README.md"
    if not readme.is_file():
        return
    for block in _FENCED.findall(readme.read_text()):
        for raw in block.splitlines():
            line = raw.strip().lstrip("$ ").strip()
            if _looks_like_command(line):
                builder.add(
                    file="README.md",
                    kind="readme_command",
                    excerpt=line,
                    reason="command in README code block",
                    confidence=0.6,
                )


def _extract_ci(repo_dir: Path, builder: EvidenceBuilder) -> None:
    workflows = repo_dir / ".github" / "workflows"
    if not workflows.is_dir():
        return
    for wf in sorted(workflows.glob("*.y*ml")):
        try:
            data = yaml.safe_load(wf.read_text()) or {}
        except yaml.YAMLError:
            continue
        for job in (data.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                run = step.get("run")
                if not run:
                    continue
                for raw in run.splitlines():
                    line = raw.strip()
                    if line:
                        builder.add(
                            file=f".github/workflows/{wf.name}",
                            kind="ci_step",
                            excerpt=line,
                            reason="CI run step",
                            confidence=0.85,
                        )


def _extract_dockerfile(repo_dir: Path, builder: EvidenceBuilder) -> None:
    if (repo_dir / "Dockerfile").is_file():
        builder.add(
            file="Dockerfile",
            kind="dockerfile",
            excerpt="Dockerfile present",
            reason="Dockerfile present",
            confidence=0.7,
        )


def _extract_compose(repo_dir: Path, builder: EvidenceBuilder) -> None:
    for name in _COMPOSE_FILES:
        if (repo_dir / name).is_file():
            builder.add(
                file=name,
                kind="compose_service",
                excerpt=f"{name} present",
                reason="compose file present",
                confidence=0.8,
            )
            return


def extract_signals(repo_dir: str | Path, builder: EvidenceBuilder) -> None:
    repo_dir = Path(repo_dir)
    _extract_readme(repo_dir, builder)
    _extract_ci(repo_dir, builder)
    _extract_dockerfile(repo_dir, builder)
    _extract_compose(repo_dir, builder)
