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
from repo_pilot.llm_planner import gather_context, propose_runbooks
from repo_pilot.llm_profiler import enrich_profile
from repo_pilot.model_client import ModelClient
from repo_pilot.planner import plan
from repo_pilot.repair import patch_fingerprint, propose_repair
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
    # repair loop (ADR-0012)
    last_logs: str
    repair_attempts: int
    tried_patches: list
    repair_history: list
    repaired: bool
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
    security: dict | None = None,
    model_client: ModelClient | None = None,
    max_repair_attempts: int = 3,
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
        # LLM enrichment when deterministic profiling is thin (unrecognized stack).
        if model_client is not None and not prof.get("frameworks") and not prof.get("entrypoints"):
            try:
                prof, prof_evidence = enrich_profile(
                    prof, gather_context(state["repo_dir"]), model_client
                )
            except Exception:
                prof_evidence = []
            evidence = [*evidence, *prof_evidence]
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
            # deterministic candidate wins; the LLM seam does not fire
            return {"runbook": _enrich(result.candidates[0], state), "visited": ["plan"]}
        if result.deferred_reason:
            # e.g. compose-only repo — deferred, not LLM-guessed (ADR-0002)
            return {"deferred_reason": result.deferred_reason, "visited": ["plan"]}

        # Tier-B gated LLM planning: only when deterministic rules found no
        # candidate (e.g. a non-Node / unconventional stack). The LLM proposes full
        # Runbook candidates from profile + evidence + files; the sandbox still
        # verifies them. A model error (e.g. missing API key) degrades to no
        # candidate, never a crash.
        if model_client is not None:
            try:
                context = gather_context(state["repo_dir"])
                llm_candidates, llm_evidence = propose_runbooks(
                    state["profile"], state["evidence"], context, model_client
                )
            except Exception:
                llm_candidates, llm_evidence = [], []
            if llm_candidates:
                evidence = [*state["evidence"], *llm_evidence]
                for item in llm_evidence:
                    validate_evidence(item)
                write_evidence(state["evidence_path"], evidence)
                best = max(llm_candidates, key=lambda c: c["confidence"])
                return {
                    "runbook": _enrich(best, state),
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
            "last_logs": logs,  # fed to the repair loop
            "visited": ["verify"],
        }

    def _repair(state: State) -> dict:
        # Cyclic agent step (ADR-0012): diagnose the failure and patch the Runbook,
        # rules-first then LLM. Bounded by max_repair_attempts + no-repeat hashing;
        # the sandbox re-verifies whatever we propose.
        runbook = state.get("runbook")
        if runbook is None:
            return {"repaired": False, "visited": ["repair"]}
        proposal = propose_repair(runbook, state.get("last_logs", ""), model_client)
        if proposal is None:
            return {"repaired": False, "visited": ["repair"]}
        patched, description, source = proposal

        tried = state.get("tried_patches", [])
        fingerprint = patch_fingerprint(patched)
        if fingerprint in tried:  # already tried this exact patch — stop looping
            return {"repaired": False, "visited": ["repair"]}

        attempt_no = state.get("repair_attempts", 0) + 1
        history = state.get("repair_history", [])
        entry = {
            "attempt": attempt_no,
            "diagnosis": description,
            "patch": description,
            "source": source,
            "result": "applied; re-verifying",
        }
        return {
            "runbook": _enrich(patched, state),
            "repair_attempts": attempt_no,
            "tried_patches": [*tried, fingerprint],
            "repair_history": [*history, entry],
            "repaired": True,
            "visited": ["repair"],
        }

    def _route_after_verify(state: State) -> str:
        if state.get("verified"):
            return "discover"
        if state.get("runbook") is not None and (
            state.get("repair_attempts", 0) < max_repair_attempts
        ):
            return "repair"
        return "report"

    def _route_after_repair(state: State) -> str:
        return "verify" if state.get("repaired") else "report"

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
            if state.get("repair_history"):
                runbook["repair_history"] = state["repair_history"]
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
    graph.add_node("repair", _repair)
    graph.add_node("discover", _discover)
    graph.add_node("test", _test)
    graph.add_node("report", _report)

    graph.add_edge(START, "clone")
    graph.add_edge("clone", "profile")
    graph.add_edge("profile", "plan")
    graph.add_edge("plan", "verify")
    # cyclic repair agent: verify branches to discover (ok) / repair (retry) / report
    graph.add_conditional_edges(
        "verify", _route_after_verify,
        {"discover": "discover", "repair": "repair", "report": "report"},
    )
    graph.add_conditional_edges(
        "repair", _route_after_repair, {"verify": "verify", "report": "report"}
    )
    graph.add_edge("discover", "test")
    graph.add_edge("test", "report")
    graph.add_edge("report", END)

    return graph.compile()
