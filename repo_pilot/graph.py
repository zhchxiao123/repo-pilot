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
from repo_pilot.healthcheck import run_healthcheck
from repo_pilot.planner import plan
from repo_pilot.report import render_report
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


def build_graph(
    executor: SandboxExecutor,
    *,
    healthcheck_retries: int = 0,
    poll_interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
):
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

    def _plan(state: State) -> dict:
        result = plan(state["profile"], state["evidence"])
        if result.candidates:
            return {"runbook": result.candidates[0], "visited": ["plan"]}
        return {"deferred_reason": result.deferred_reason, "visited": ["plan"]}

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
        logs = sandbox.logs

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
        )
        Path(state["report_path"]).write_text(markdown)
        return {"report": markdown, "visited": ["report"]}

    def _passthrough(name: str):
        def node(_state: State) -> dict:
            return {"visited": [name]}

        return node

    graph = StateGraph(State)
    graph.add_node("clone", _clone)
    graph.add_node("profile", _profile)
    graph.add_node("plan", _plan)
    graph.add_node("verify", _verify)
    graph.add_node("discover", _discover)
    graph.add_node("test", _passthrough("test"))
    graph.add_node("report", _report)

    graph.add_edge(START, "clone")
    for prev, nxt in zip(MACRO_PHASES, MACRO_PHASES[1:]):
        graph.add_edge(prev, nxt)
    graph.add_edge("report", END)

    return graph.compile()
