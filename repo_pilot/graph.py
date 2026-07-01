"""The macro-skeleton graph (ADR-0006).

A fixed LangGraph DAG over clone -> profile -> plan -> verify -> discover -> test ->
report. The Sandbox Executor is injected so the verify phase runs against either
the real Docker executor or a fake, keeping the pipeline testable with no Docker
(ADR-0004). The plan phase builds evidence-based candidate Runbooks via the
planner; a compose-only repo is deferred rather than failed.

State is the thin, typed Runbook-spine plus a ``visited`` execution trace.
"""

from __future__ import annotations

import json
import operator
import time
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

import yaml
from langgraph.graph import END, START, StateGraph

from repo_pilot import profiler
from repo_pilot.cloner import RepoCloner, RepoRef
from repo_pilot.compose import compile_compose, iter_step_commands
from repo_pilot.discovery import discover_targets
from repo_pilot.evidence import EvidenceBuilder, write_evidence
from repo_pilot.executor import SandboxExecutor
from repo_pilot.extractors import extract_signals
from repo_pilot.confidence import confidence
from repo_pilot.healthcheck import run_healthcheck
from repo_pilot.model_client import ModelClient
from repo_pilot.nl_extract import nl_extract_commands
from repo_pilot.planner import default_healthcheck, plan
from repo_pilot.report import render_report
from repo_pilot.security import default_security, dummy_env, redact
from repo_pilot.smoke import generate_smoke_tests, run_smoke_tests
from repo_pilot.schemas import validate_evidence, validate_profile, validate_runbook

MACRO_PHASES = ["clone", "profile", "plan", "verify", "discover", "test", "report"]


class State(TypedDict, total=False):
    # inputs
    repo_url: str
    commit: str | None
    repo_dir: str
    report_path: str
    runbook_path: str
    profile_path: str
    evidence_path: str
    # Runbook-spine slots
    repo_ref: RepoRef
    profile: Any
    evidence: list
    runbook: dict
    deferred_reason: str | None
    attempts: list
    verified: bool
    sandbox: Any
    targets: list
    tests: list
    report: str
    # execution trace
    visited: Annotated[list[str], operator.add]


def initial_state(
    *,
    repo_url: str,
    commit: str | None,
    repo_dir: str,
    report_path: str,
    runbook_path: str,
    profile_path: str,
    evidence_path: str,
) -> State:
    return {
        "repo_url": repo_url,
        "commit": commit,
        "repo_dir": repo_dir,
        "report_path": report_path,
        "runbook_path": runbook_path,
        "profile_path": profile_path,
        "evidence_path": evidence_path,
        "evidence": [],
        "attempts": [],
        "verified": False,
        "targets": [],
        "tests": [],
        "visited": [],
    }


def _reproduce(repo_url: str, runbook: dict) -> list[str]:
    # Clone into an explicit `repo` dir so the following `cd repo` is correct.
    return [f"git clone {repo_url} repo", "cd repo", *iter_step_commands(runbook)]


def _nl_runbook(repo: dict, commands: list[str], ev_id: str) -> dict:
    """Build a low-confidence candidate from NL-extracted commands (Tier-B)."""
    tool = commands[0].split(" ", 1)[0]
    if tool in ("python", "pip", "uv", "flask", "uvicorn", "django-admin"):
        image = "python:3.11-bookworm"
    elif tool in ("node", "npm", "pnpm", "yarn"):
        image = "node:20-bookworm"
    else:
        image = "debian:stable-slim"
    setup = [{"command": c} for c in commands[:-1]]
    return {
        "schema_version": "v1",
        "id": "nl_readme",
        "status": "candidate",
        "confidence": confidence(["llm_inference"]),
        "evidence_refs": [ev_id],
        "repo": repo,
        "runtime": {
            "image": image,
            "workdir": "/workspace/repo",
            "resources": {"cpu": 2, "memory": "4g", "pids": 512, "timeout_seconds": 900},
        },
        "steps": {
            "setup": setup,
            "start": [{"command": commands[-1], "expected_ports": [8000]}],
        },
        "healthcheck": default_healthcheck(),
    }


def build_graph(
    executor: SandboxExecutor,
    *,
    security: dict | None = None,
    model_client: ModelClient | None = None,
    healthcheck_retries: int = 0,
    poll_interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
):
    sec = security if security is not None else default_security()
    def _clone(state: State) -> dict:
        ref = RepoCloner().clone(
            state["repo_url"], commit=state.get("commit"), dest=state["repo_dir"]
        )
        return {"repo_ref": ref, "visited": ["clone"]}

    def _profile(state: State) -> dict:
        builder = EvidenceBuilder()
        prof, _ = profiler.profile(state["repo_dir"], builder)
        extract_signals(state["repo_dir"], builder)
        evidence = builder.items
        prof["repo"] = {
            "url": state["repo_url"],
            "commit": state["repo_ref"].commit,
        }
        validate_profile(prof)
        for item in evidence:
            validate_evidence(item)
        Path(state["profile_path"]).write_text(json.dumps(prof, indent=2))
        write_evidence(state["evidence_path"], evidence)
        return {"profile": prof, "evidence": evidence, "visited": ["profile"]}

    def _enrich(runbook: dict, state: State) -> dict:
        runbook["security"] = dict(sec)
        env = dummy_env(state["repo_dir"])  # dummy values only — never real secrets
        if env:
            runbook["env"] = {"generated": env}
        return runbook

    def _plan(state: State) -> dict:
        result = plan(state["profile"], state["evidence"])
        if result.candidates:
            # deterministic candidate wins; the NL seam does not fire
            return {"runbook": _enrich(result.candidates[0], state), "visited": ["plan"]}
        if result.deferred_reason:
            return {"deferred_reason": result.deferred_reason, "visited": ["plan"]}

        # Tier-B gated fallback: only when deterministic planning found nothing
        readme = Path(state["repo_dir"]) / "README.md"
        if model_client is not None and readme.is_file():
            commands = nl_extract_commands(readme.read_text(), model_client)
            if commands:
                ev_id = "ev_nl1"
                ev = {
                    "id": ev_id,
                    "file": "README.md",
                    "line": None,
                    "kind": "llm_inference",
                    "excerpt": "; ".join(commands),
                    "reason": "run commands extracted from README prose",
                    "confidence": 0.3,
                }
                validate_evidence(ev)
                evidence = [*state["evidence"], ev]
                write_evidence(state["evidence_path"], evidence)
                runbook = _nl_runbook(state["profile"]["repo"], commands, ev_id)
                return {
                    "runbook": _enrich(runbook, state),
                    "evidence": evidence,
                    "visited": ["plan"],
                }

        return {"deferred_reason": None, "visited": ["plan"]}

    def _verify(state: State) -> dict:
        if state.get("runbook") is None:
            return {"verified": False, "visited": ["verify"]}
        runbook = dict(state["runbook"])
        # The sandbox stays up on success so discover/test can use the live app;
        # it is stopped in the report phase. On failure it is stopped immediately.
        sandbox = executor.start(
            compile_compose(runbook), repo_dir=str(state["repo_ref"].repo_dir)
        )
        result = run_healthcheck(
            sandbox,
            runbook.get("healthcheck", {}),
            retries=healthcheck_retries,
            poll_interval=poll_interval,
            sleep=sleep,
        )
        ports = dict(sandbox.ports)
        logs = redact(sandbox.logs)  # scrub secrets before anything is stored (§20.2)

        attempt = {"healthcheck_passed": result.passed, "logs_summary": logs}
        if result.passed:
            runbook["status"] = "verified"
            runbook["verification"] = {
                "ports": [{"container": c, "host": h} for c, h in ports.items()],
                "healthcheck_result": {
                    "passed": True,
                    "url": result.url,
                    "status_code": result.status_code,
                },
                "logs_summary": logs,
                "reproduce": _reproduce(state["repo_url"], runbook),
            }
            return {
                "runbook": runbook,
                "verified": True,
                "attempts": [attempt],
                "sandbox": sandbox,
                "visited": ["verify"],
            }

        sandbox.stop()
        runbook["status"] = "failed"
        runbook["verification"] = {
            "healthcheck_result": {"passed": False},
            "logs_summary": logs,
            "ports": [{"container": c, "host": h} for c, h in ports.items()],
        }
        return {
            "runbook": runbook,
            "verified": False,
            "attempts": [attempt],
            "visited": ["verify"],
        }

    def _discover(state: State) -> dict:
        sandbox = state.get("sandbox")
        if sandbox is None:
            return {"visited": ["discover"]}
        fallback = state["runbook"].get("healthcheck", {}).get("url_candidates")
        # Never let a discovery error abort the graph — the report phase must still
        # run to tear the sandbox down (avoids a container/volume leak).
        try:
            targets = discover_targets(sandbox, fallback_paths=fallback)
        except Exception:
            targets = []
        return {"targets": targets, "visited": ["discover"]}

    def _test(state: State) -> dict:
        sandbox = state.get("sandbox")
        targets = state.get("targets") or []
        if sandbox is None or not targets:
            return {"visited": ["test"]}
        try:
            tests = run_smoke_tests(sandbox, generate_smoke_tests(targets))
        except Exception:
            tests = []
        return {"tests": tests, "visited": ["test"]}

    def _report(state: State) -> dict:
        sandbox = state.get("sandbox")
        if sandbox is not None:
            sandbox.stop()
        runbook = state.get("runbook")
        if runbook is not None:
            validate_runbook(runbook)
            Path(state["runbook_path"]).write_text(
                yaml.safe_dump(runbook, sort_keys=True)
            )
        markdown = render_report(
            state["repo_url"],
            state["repo_ref"],
            runbook=runbook,
            deferred_reason=state.get("deferred_reason"),
            targets=state.get("targets"),
            tests=state.get("tests"),
        )
        Path(state["report_path"]).write_text(markdown)
        return {"report": markdown, "visited": ["report"]}

    graph = StateGraph(State)
    graph.add_node("clone", _clone)
    graph.add_node("profile", _profile)
    graph.add_node("plan", _plan)
    graph.add_node("verify", _verify)
    graph.add_node("discover", _discover)
    graph.add_node("test", _test)
    graph.add_node("report", _report)

    graph.add_edge(START, "clone")
    for prev, nxt in zip(MACRO_PHASES, MACRO_PHASES[1:]):
        graph.add_edge(prev, nxt)
    graph.add_edge("report", END)

    return graph.compile()
