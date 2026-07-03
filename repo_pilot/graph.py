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
from repo_pilot.compose import iter_step_commands
from repo_pilot.discovery import discover_targets
from repo_pilot.evidence import EvidenceBuilder, write_evidence
from repo_pilot.executor import SandboxExecutor
from repo_pilot.extractors import extract_signals
from repo_pilot.explore_tools import RepoTools, seed_context
from repo_pilot.plan_agent import _AGENT_EVIDENCE_ID, explore_and_plan
from repo_pilot.model_client import ModelClient
from repo_pilot.candidate_planning import plan_candidates
from repo_pilot.run_verifier import verify_run_plan
from repo_pilot.runbook_projection import plan_to_runbook, runbook_to_plan
from repo_pilot.repair import patch_fingerprint, propose_repair
from repo_pilot.report import render_report
from repo_pilot.security import default_security, dummy_env
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
    candidates: list
    classification: str
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
    chat_model: Any = None,
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
        # Deterministic candidates are canonical RunPlans, projected to a v1
        # runbook only for the persisted state/artifact (ADR-0019).
        result = plan_candidates(state["profile"], state["evidence"])
        if result.candidates:
            runbook = plan_to_runbook(result.candidates[0])
            return {
                "runbook": _enrich(runbook, state),
                "classification": result.classification,
                "visited": ["plan"],
            }
        if result.deferred_reason:
            # e.g. compose-only repo — deferred, not guessed (ADR-0002)
            return {"deferred_reason": result.deferred_reason, "visited": ["plan"]}

        # The stack isn't rule-recognized: the plan AGENT explores the repo itself
        # (read-only tools) and decides. It also classifies the repo, so a
        # non-service is reported honestly rather than as a failure. The sandbox
        # still adjudicates any candidate it proposes (ADR-0004/0016).
        if chat_model is None:
            return {"deferred_reason": None, "visited": ["plan"]}
        try:
            agent = explore_and_plan(
                chat_model,
                RepoTools(state["repo_ref"].repo_dir),
                seed_context(state["repo_dir"]),
                state["profile"]["repo"],
            )
        except Exception:
            return {"deferred_reason": None, "classification": "unknown", "visited": ["plan"]}

        if agent.candidates:
            ev = {
                "id": _AGENT_EVIDENCE_ID,
                "file": "(agent)",
                "line": None,
                "kind": "llm_inference",
                "excerpt": (agent.rationale or "agent-proposed run plan")[:500],
                "reason": "plan agent explored the repo and proposed a run plan",
                "confidence": 0.3,
            }
            validate_evidence(ev)
            evidence = [*state["evidence"], ev]
            write_evidence(state["evidence_path"], evidence)
            return {
                "runbook": _enrich(agent.candidates[0], state),
                "candidates": agent.candidates,
                "classification": agent.classification,
                "evidence": evidence,
                "visited": ["plan"],
            }

        # no runnable candidate — but the agent may have classified why
        reason = None
        if agent.classification in ("library", "cli", "docs", "monorepo"):
            reason = f"not-a-service:{agent.classification}"
        return {
            "deferred_reason": reason,
            "classification": agent.classification,
            "visited": ["plan"],
        }

    def _verify(state: State) -> dict:
        # One verifier interface (run_verifier): the runbook is imported as a
        # canonical RunPlan and every component's oracle is adjudicated against the
        # sandbox. This node only projects the resulting facts back onto the
        # persisted runbook; it does not know single-app vs component semantics.
        if state.get("runbook") is None:
            return {"verified": False, "visited": ["verify"]}
        runbook = dict(state["runbook"])
        result = verify_run_plan(
            runbook_to_plan(runbook),
            executor,
            repo_dir=str(state["repo_ref"].repo_dir),
            retries=healthcheck_retries,
            poll_interval=poll_interval,
            sleep=sleep,
        )
        logs = result.logs_summary
        attempt = {"healthcheck_passed": result.verified, "logs_summary": logs}
        verification = {
            "healthcheck_result": {"passed": result.verified},
            "logs_summary": logs,
            "ports": result.ports,
            "components": [
                {"name": r.name, "oracle": r.oracle, "passed": r.passed, "detail": r.detail}
                for r in result.component_results
            ],
        }
        if result.verified:
            # sandbox stays up on success so discover/test can use the live app
            runbook["status"] = "verified"
            verification["reproduce"] = _reproduce(state["repo_url"], runbook)
            runbook["verification"] = verification
            return {
                "runbook": runbook,
                "verified": True,
                "attempts": [attempt],
                "sandbox": result.sandbox,
                "visited": ["verify"],
            }
        runbook["status"] = "failed"
        runbook["verification"] = verification
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
        proposal = propose_repair(
            runbook_to_plan(runbook), state.get("last_logs", ""), model_client
        )
        if proposal is None:
            return {"repaired": False, "visited": ["repair"]}

        tried = state.get("tried_patches", [])
        fingerprint = patch_fingerprint(proposal.plan)
        if fingerprint in tried:  # already tried this exact patch — stop looping
            return {"repaired": False, "visited": ["repair"]}

        attempt_no = state.get("repair_attempts", 0) + 1
        history = state.get("repair_history", [])
        entry = {
            "attempt": attempt_no,
            "diagnosis": proposal.description,
            "patch": proposal.description,
            "source": proposal.source,
            "result": "applied; re-verifying",
        }
        return {
            "runbook": _enrich(plan_to_runbook(proposal.plan), state),
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
            classification=state.get("classification"),
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
