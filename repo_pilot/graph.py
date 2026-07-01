"""The macro-skeleton graph (ADR-0006).

A fixed LangGraph DAG over the phases clone -> profile -> plan -> verify ->
discover -> test -> report. In this slice every phase node except ``clone`` and
``report`` is a passthrough; later slices fill them in with autonomous agents.

State is the thin, typed Runbook-spine: inputs plus the spine slots plus a
``visited`` execution trace.
"""

from __future__ import annotations

import operator
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from repo_pilot.cloner import RepoCloner, RepoRef
from repo_pilot.report import render_report

MACRO_PHASES = ["clone", "profile", "plan", "verify", "discover", "test", "report"]


class State(TypedDict, total=False):
    # inputs
    repo_url: str
    commit: str | None
    repo_dir: str
    report_path: str
    # Runbook-spine slots
    repo_ref: RepoRef
    profile: Any
    evidence: list
    runbook: Any
    attempts: list
    verified: bool
    targets: list
    tests: list
    report: str
    # execution trace
    visited: Annotated[list[str], operator.add]


def initial_state(
    *, repo_url: str, commit: str | None, repo_dir: str, report_path: str
) -> State:
    return {
        "repo_url": repo_url,
        "commit": commit,
        "repo_dir": repo_dir,
        "report_path": report_path,
        "evidence": [],
        "attempts": [],
        "verified": False,
        "targets": [],
        "tests": [],
        "visited": [],
    }


def _clone(state: State) -> dict:
    ref = RepoCloner().clone(
        state["repo_url"], commit=state.get("commit"), dest=state["repo_dir"]
    )
    return {"repo_ref": ref, "visited": ["clone"]}


def _report(state: State) -> dict:
    markdown = render_report(state["repo_url"], state["repo_ref"])
    Path(state["report_path"]).write_text(markdown)
    return {"report": markdown, "visited": ["report"]}


def _passthrough(name: str):
    def node(_state: State) -> dict:
        return {"visited": [name]}

    return node


def build_graph():
    graph = StateGraph(State)
    graph.add_node("clone", _clone)
    for phase in ("profile", "plan", "verify", "discover", "test"):
        graph.add_node(phase, _passthrough(phase))
    graph.add_node("report", _report)

    graph.add_edge(START, "clone")
    for prev, nxt in zip(MACRO_PHASES, MACRO_PHASES[1:]):
        graph.add_edge(prev, nxt)
    graph.add_edge("report", END)

    return graph.compile()
