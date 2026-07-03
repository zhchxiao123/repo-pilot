"""Canonical candidate planning (Task 6).

Produces ranked canonical ``RunPlan``s from a Profile + Evidence (and, later, an
LLM agent result). This is the canonical sibling of the legacy ``planner.plan``:
where that returns v1 runbook dicts, ``plan_candidates`` returns ``RunPlan``s so
the rest of the pipeline can operate on the canonical model.

Setup is folded into the component command (``npm install && npm start``) — the
same convention the canonical verifier tests use — because a ``RunComponent`` runs
a single foreground command. The graph adopts this entry point in Task 11; until
then ``planner.plan`` remains the deterministic path that writes v1 artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repo_pilot.confidence import confidence
from repo_pilot.planner import (
    NEEDS_COMPOSE,
    _expected_port,
    _install_steps,
    _start_command,
)
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, RunShape
from repo_pilot.shape_detection import detect_shapes

_NODE_IMAGE = "node:20-bookworm"
_WORKDIR = "/workspace/repo"
# Statuses HTTP services commonly answer with before app routes are wired.
_ACCEPTABLE_STATUS = [200, 204, 301, 302, 404]


@dataclass
class PlanningResult:
    candidates: list[RunPlan] = field(default_factory=list)
    deferred_reason: str | None = None
    classification: str | None = None


def _node_service_plans(
    profile: dict, evidence: list[dict], service_entries: list[dict]
) -> list[RunPlan]:
    kind_by_id = {e["id"]: e["kind"] for e in evidence if "id" in e}
    profile_refs = profile.get("evidence_refs", {})
    managers = profile.get("package_managers", ["npm"])
    manager = managers[0] if managers else "npm"
    port = _expected_port(profile.get("frameworks", []))
    repo = profile.get("repo")

    plans: list[RunPlan] = []
    for entry in service_entries:
        key = entry["key"]
        # fold install(s) + start into one foreground command
        install = " && ".join(step["command"] for step in _install_steps(manager))
        start = _start_command(manager, key)
        command = f"{install} && {start}" if install else start

        refs = list(entry.get("evidence_refs", []))
        refs += profile_refs.get(f"package_manager:{manager}", [])
        refs = list(dict.fromkeys(refs))  # de-dup, preserve order
        kinds = [kind_by_id[r] for r in refs if r in kind_by_id]

        plans.append(
            RunPlan(
                id=f"node_{manager}_{key}",
                shape=RunShape.SERVICE,
                confidence=confidence(kinds),
                evidence_refs=refs,
                repo=repo,
                source="deterministic",
                components=[
                    RunComponent(
                        name="app",
                        image=_NODE_IMAGE,
                        workdir=_WORKDIR,
                        command=command,
                        ports=[port],
                        oracle=Oracle(
                            type="http", port=port, path="/",
                            acceptable_status=list(_ACCEPTABLE_STATUS),
                        ),
                    )
                ],
            )
        )
    plans.sort(key=lambda p: (p.confidence or 0.0), reverse=True)
    return plans


def plan_candidates(
    profile: dict, evidence: list[dict], agent_result: object = None
) -> PlanningResult:
    """Rank canonical RunPlans for a repo, or defer/return empty.

    Deterministic shape detection gates generation: a Node *service* (a start/dev
    script) becomes ranked service RunPlans; a compose-only repo defers; anything
    else yields no deterministic candidate (the LLM planner handles it upstream).
    """
    hints = detect_shapes(profile, evidence)
    entrypoints = profile.get("entrypoints", [])
    service_entries = [
        e for e in entrypoints
        if e.get("type") == "script" and e.get("key") in ("start", "dev")
    ]
    if service_entries and hints.primary.shape == RunShape.SERVICE:
        plans = _node_service_plans(profile, evidence, service_entries)
        return PlanningResult(candidates=plans, classification="service")
    if any(e.get("kind") == "compose_service" for e in evidence):
        return PlanningResult(deferred_reason=NEEDS_COMPOSE)
    return PlanningResult()
