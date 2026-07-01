"""Runbook Planner (§7, §14.4).

Builds candidate Runbooks from a Profile + Evidence, each carrying evidence_refs
and a deterministic confidence score (ADR-0011). Candidates are ranked by
confidence. When nothing runnable is found but a compose file is present, the repo
is deferred rather than silently failed. Rules-first; no LLM in this slice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repo_pilot.confidence import confidence

_DEFAULT_PORT = 3000
_FRAMEWORK_PORTS = {"vite": 5173, "nextjs": 3000, "express": 3000}

NEEDS_COMPOSE = "needs-compose"

# evidence kinds that can corroborate a candidate's run command
_CORROBORATING_KINDS = ("readme_command", "ci_step")


@dataclass
class PlanResult:
    candidates: list[dict] = field(default_factory=list)
    deferred_reason: str | None = None


def _install_steps(manager: str) -> list[dict]:
    if manager == "pnpm":
        return [{"command": "corepack enable"}, {"command": "pnpm install --frozen-lockfile"}]
    if manager == "yarn":
        return [{"command": "yarn install"}]
    return [{"command": "npm install"}]


def _start_command(manager: str, key: str) -> str:
    if manager == "pnpm":
        return f"pnpm {key}"
    if manager == "yarn":
        return f"yarn {key}"
    return "npm start" if key == "start" else f"npm run {key}"


def _expected_port(frameworks: list[str]) -> int:
    for framework in frameworks:
        if framework in _FRAMEWORK_PORTS:
            return _FRAMEWORK_PORTS[framework]
    return _DEFAULT_PORT


def plan(profile: dict, evidence: list[dict]) -> PlanResult:
    entrypoints = profile.get("entrypoints", [])
    if not entrypoints:
        if any(e.get("kind") == "compose_service" for e in evidence):
            return PlanResult(deferred_reason=NEEDS_COMPOSE)
        return PlanResult()

    kind_by_id = {e["id"]: e["kind"] for e in evidence}
    profile_refs = profile.get("evidence_refs", {})
    managers = profile.get("package_managers", ["npm"])
    manager = managers[0] if managers else "npm"
    frameworks = profile.get("frameworks", [])
    port = _expected_port(frameworks)

    candidates: list[dict] = []
    for entry in entrypoints:
        key = entry["key"]
        command = _start_command(manager, key)
        refs = list(entry.get("evidence_refs", []))
        refs += profile_refs.get(f"package_manager:{manager}", [])
        # corroboration: README/CI evidence that mentions this run command
        refs += [
            e["id"]
            for e in evidence
            if e["kind"] in _CORROBORATING_KINDS and command in e["excerpt"]
        ]
        refs = list(dict.fromkeys(refs))  # de-dup, preserve order
        kinds = [kind_by_id[r] for r in refs if r in kind_by_id]

        candidates.append(
            {
                "schema_version": "v1",
                "id": f"node_{manager}_{key}",
                "status": "candidate",
                "confidence": confidence(kinds),
                "evidence_refs": refs,
                "repo": profile["repo"],
                "runtime": {"image": "node:20-bookworm", "workdir": "/workspace/repo"},
                "steps": {
                    "setup": _install_steps(manager),
                    "start": [{"command": command, "expected_ports": [port]}],
                },
                "healthcheck": {
                    "strategy": "http",
                    "url_candidates": ["/health", "/api/health", "/"],
                    "acceptable_status": [200, 204, 301, 302, 404],
                },
            }
        )

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return PlanResult(candidates=candidates)
