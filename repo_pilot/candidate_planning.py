"""Canonical candidate planning.

Produces ranked canonical ``RunPlan``s from a Profile + Evidence. Where the legacy
``planner.plan`` returns v1 runbook dicts, ``plan_candidates`` returns ``RunPlan``s
so the rest of the pipeline operates on the canonical model.

Planning is **language-aware**: the image, install, and run command are resolved
from the repo's ecosystem (Python / Go / Node), not hardcoded to Node. Setup is
folded into the component command (a ``RunComponent`` runs one foreground command).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repo_pilot.compose_import import import_compose
from repo_pilot.confidence import confidence
from repo_pilot.planner import NEEDS_COMPOSE, _expected_port, _install_steps, _start_command
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, RunShape
from repo_pilot.shape_detection import detect_shapes

_NODE_IMAGE = "node:20-bookworm"
_WORKDIR = "/workspace/repo"
# Statuses HTTP services commonly answer with before app routes are wired.
_ACCEPTABLE_STATUS = [200, 204, 301, 302, 404]


@dataclass(frozen=True)
class _Ecosystem:
    """Everything planning needs to know about a repo's toolchain, in one place —
    so adding an ecosystem is one registry entry, not edits scattered across five
    helpers. `service_port` is None when the port comes from the web framework;
    `tools` are the command first-tokens that identify this ecosystem."""

    tag: str
    image: str
    manager_key: str
    service_port: int | None
    files: frozenset[str] = frozenset()
    tools: frozenset[str] = frozenset()


# `make` is a build-tool pseudo-ecosystem for Makefile repos with no language
# manifest (buildpack-deps ships make + gcc). Order matters: more specific files
# (go.mod/Makefile) are matched before the generic language fallback.
_ECOSYSTEMS: dict[str, _Ecosystem] = {
    "make": _Ecosystem("make", "buildpack-deps:bookworm", "make", None,
                        files=frozenset({"Makefile"}), tools=frozenset({"make"})),
    "go": _Ecosystem("go", "golang:1.22", "go", 8080,
                     files=frozenset({"go.mod"}), tools=frozenset({"go"})),
    "python": _Ecosystem("python", "python:3.11", "pip", 8000,
                         files=frozenset({"pyproject.toml", "requirements.txt"}),
                         tools=frozenset({"python", "python3", "flask", "uvicorn",
                                          "gunicorn", "hypercorn", "daphne", "pytest",
                                          "pip", "django-admin", "manage.py"})),
    "node": _Ecosystem("node", _NODE_IMAGE, "npm", None,
                       tools=frozenset({"npm", "pnpm", "yarn", "node", "npx"})),
}
_LANG_TO_ECO = {"python": "python", "go": "go", "javascript": "node", "typescript": "node"}


@dataclass
class PlanningResult:
    candidates: list[RunPlan] = field(default_factory=list)
    deferred_reason: str | None = None
    classification: str | None = None


def _ecosystem(profile: dict, entry: dict) -> _Ecosystem:
    """Resolve the ecosystem for an entrypoint. The profile languages (derived from
    manifests) are the authoritative signal; the entry command/file disambiguates a
    polyglot repo. This is what keeps a Go CLI off a Node image."""
    tokens = (entry.get("command") or "").split()
    first, file = (tokens[0] if tokens else ""), (entry.get("file") or "")
    for eco in _ECOSYSTEMS.values():
        if file in eco.files or first in eco.tools:
            return eco
    # fall back to the manifest-detected language(s), most specific first
    langs = profile.get("languages", [])
    for lang in ("go", "python", "javascript", "typescript"):
        if lang in langs:
            return _ECOSYSTEMS[_LANG_TO_ECO[lang]]
    return _ECOSYSTEMS["node"]


def _install_command(profile: dict, entry: dict, eco: _Ecosystem) -> str | None:
    """The install step for an ecosystem, or None when the run command builds from
    source (`go run`) or drives its own build (`make`)."""
    if eco.tag in ("go", "make"):
        return None
    if eco.tag == "python":
        if entry.get("file") == "requirements.txt":
            return "pip install -r requirements.txt"
        return "pip install -e ."
    managers = profile.get("package_managers", ["npm"])
    manager = managers[0] if managers else "npm"
    return " && ".join(s["command"] for s in _install_steps(manager))


def _fold_install(install: str | None, run: str) -> str:
    return f"{install} && {run}" if install else run


def _run_command(profile: dict, entry: dict, eco: _Ecosystem) -> str:
    """The foreground run command. A Node package *script* is invoked via the
    package manager; an inferred Python/Go entry already carries its real command."""
    if entry.get("type") == "script" and eco.tag == "node":
        managers = profile.get("package_managers", ["npm"])
        manager = managers[0] if managers else "npm"
        return _start_command(manager, entry["key"])
    return entry["command"]


def _service_port(profile: dict, eco: _Ecosystem) -> int:
    if eco.service_port is not None:
        return eco.service_port
    return _expected_port(profile.get("frameworks", []))


def _refs(profile: dict, entry: dict, eco: _Ecosystem) -> list[str]:
    refs = list(entry.get("evidence_refs", []))
    # Node keeps its concrete manager (npm/pnpm/yarn) for evidence lookup.
    manager_key = eco.manager_key
    if eco.tag == "node":
        managers = profile.get("package_managers", ["npm"])
        manager_key = managers[0] if managers else "npm"
    refs += profile.get("evidence_refs", {}).get(f"package_manager:{manager_key}", [])
    return list(dict.fromkeys(refs))


def _confidence(evidence: list[dict], refs: list[str]) -> float:
    kind_by_id = {e["id"]: e["kind"] for e in evidence if "id" in e}
    return confidence([kind_by_id[r] for r in refs if r in kind_by_id])


def _service_plans(profile: dict, evidence: list[dict], service_entries: list[dict]) -> list[RunPlan]:
    repo = profile.get("repo")
    plans: list[RunPlan] = []
    for entry in service_entries:
        eco = _ecosystem(profile, entry)
        install = _install_command(profile, entry, eco)
        command = _fold_install(install, _run_command(profile, entry, eco))
        port = _service_port(profile, eco)
        refs = _refs(profile, entry, eco)
        plans.append(
            RunPlan(
                id=f"{eco.tag}_service_{entry['key']}",
                shape=RunShape.SERVICE,
                confidence=_confidence(evidence, refs),
                evidence_refs=refs,
                repo=repo,
                source="deterministic",
                components=[
                    RunComponent(
                        name="app", image=eco.image, workdir=_WORKDIR,
                        command=command, ports=[port],
                        oracle=Oracle(type="http", port=port, path="/health",
                                      acceptable_status=list(_ACCEPTABLE_STATUS)),
                    )
                ],
            )
        )
    plans.sort(key=lambda p: (p.confidence or 0.0), reverse=True)
    return plans


def _cli_plans(profile: dict, evidence: list[dict], bin_entries: list[dict]) -> list[RunPlan]:
    """A CLI is exercised by installing then running its command to a clean exit."""
    repo = profile.get("repo")
    plans: list[RunPlan] = []
    for entry in bin_entries:
        eco = _ecosystem(profile, entry)
        install = _install_command(profile, entry, eco)
        command = _fold_install(install, entry["command"])
        refs = _refs(profile, entry, eco)
        plans.append(
            RunPlan(
                id=f"cli_{entry.get('key', 'run')}",
                shape=RunShape.CLI,
                confidence=_confidence(evidence, refs),
                evidence_refs=refs,
                repo=repo,
                source="deterministic",
                components=[
                    RunComponent(
                        name="cli", image=eco.image, workdir=_WORKDIR,
                        command=command, oracle=Oracle(type="functional-smoke"),
                    )
                ],
            )
        )
    return plans


def _exercise_plans(
    profile: dict, evidence: list[dict], entries: list[dict],
    shape: RunShape, oracle_type: str, name: str,
) -> list[RunPlan]:
    """Non-service shapes (library/build/batch) succeed by being *exercised* to a
    clean exit: install (if any) then run the test/build/job command."""
    repo = profile.get("repo")
    plans: list[RunPlan] = []
    for entry in entries:
        eco = _ecosystem(profile, entry)
        command = _fold_install(_install_command(profile, entry, eco), entry["command"])
        refs = _refs(profile, entry, eco)
        plans.append(
            RunPlan(
                id=f"{shape.value}_{entry['key']}",
                shape=shape,
                confidence=_confidence(evidence, refs),
                evidence_refs=refs,
                repo=repo,
                source="deterministic",
                components=[
                    RunComponent(name=name, image=eco.image, workdir=_WORKDIR,
                                 command=command, oracle=Oracle(type=oracle_type)),
                ],
            )
        )
    return plans


def plan_candidates(
    profile: dict,
    evidence: list[dict],
    agent_result: object = None,
    repo_dir: str | None = None,
) -> PlanningResult:
    """Rank canonical RunPlans for a repo, or defer/return empty.

    Deterministic shape detection gates generation: a service (Node script or an
    inferred Python/Go start) becomes ranked service RunPlans; a CLI (binary
    entrypoint) becomes CLI RunPlans; anything else yields no deterministic
    candidate (the LLM planner handles it upstream). A compose-first repo (given
    ``repo_dir`` to read the file from) goes through the **controlled compose
    import**: a safe subset becomes a runnable plan, unsafe compose defers as
    ``unsafe-compose``, anything beyond the subset as ``needs-compose`` — the
    target's compose file is never executed verbatim (ADR-0002).
    """
    hints = detect_shapes(profile, evidence)
    entrypoints = profile.get("entrypoints", [])
    service_entries = [
        e for e in entrypoints
        if e.get("key") in ("start", "dev") and e.get("type") in ("script", "inferred")
    ]
    bin_entries = [e for e in entrypoints if e.get("type") == "binary"]
    test_entries = [e for e in entrypoints if e.get("key") == "test"]
    build_entries = [e for e in entrypoints if e.get("key") == "build"]
    run_entries = [e for e in entrypoints if e.get("key") == "run"]

    shape = hints.primary.shape
    if service_entries and shape == RunShape.SERVICE:
        return PlanningResult(candidates=_service_plans(profile, evidence, service_entries),
                              classification="service")
    if bin_entries and shape == RunShape.CLI:
        return PlanningResult(candidates=_cli_plans(profile, evidence, bin_entries),
                              classification="cli")
    if test_entries and shape == RunShape.LIBRARY:
        return PlanningResult(
            candidates=_exercise_plans(profile, evidence, test_entries,
                                       RunShape.LIBRARY, "tests-pass", "lib"),
            classification="library")
    if build_entries and shape == RunShape.BUILD:
        return PlanningResult(
            candidates=_exercise_plans(profile, evidence, build_entries,
                                       RunShape.BUILD, "build-succeeds", "build"),
            classification="build")
    if run_entries and shape == RunShape.BATCH:
        return PlanningResult(
            candidates=_exercise_plans(profile, evidence, run_entries,
                                       RunShape.BATCH, "exit-zero", "batch"),
            classification="batch")
    if any(e.get("kind") == "compose_service" for e in evidence):
        if repo_dir is not None:
            imported = import_compose(repo_dir, profile=profile, evidence=evidence)
            if imported.plan is not None:
                return PlanningResult(
                    candidates=[imported.plan],
                    classification=imported.plan.shape.value,
                )
            if imported.deferred_reason is not None:
                return PlanningResult(deferred_reason=imported.deferred_reason)
        return PlanningResult(deferred_reason=NEEDS_COMPOSE)
    return PlanningResult()
