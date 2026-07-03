"""The plan agent (ADR-0016): an LLM that explores the repo and decides how to run it.

A bounded tool-calling loop. The agent is handed a seed (file tree + light profile)
and read-only exploration tools (``explore_tools``), reads whatever it needs, then
calls ``submit_plan`` with a repo classification and ranked candidate Runbooks.
Exploration and proposing are the agent's; the sandbox still adjudicates truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from repo_pilot.confidence import confidence
from repo_pilot.explore_tools import RepoTools
from repo_pilot.oracles import ORACLE_TYPES
from repo_pilot.run_shape import (
    Oracle,
    RunComponent,
    RunPlan,
    infer_shape,
    normalize_plan,
)

_AGENT_EVIDENCE_ID = "ev_agent1"
_MAX_ITERS = 25

CLASSIFICATIONS = ("service", "cli", "library", "docs", "monorepo", "unknown")

_SYSTEM = """You determine how to run a GitHub repository locally in one container.

Explore the repo with the tools (list_dir, read_file, search, find) — read the real
files (Dockerfiles, compose, Procfile, Makefile, package.json/pyproject/go.mod,
entry-point source, CI). Be skeptical: a Dockerfile or command may be for TESTS or
CI, not for running the app — judge intent from its content.

Then call submit_plan exactly once with:
- classification: one of service | cli | library | docs | monorepo | unknown
  (use service only if there is a long-running server/app to start).
- candidates: for a `service`, 1-3 ordered run plans, best first.

  A project is often a SYSTEM of components (frontend + backend + db + cache +
  worker + ...), not one process. When more than one process must run, describe
  each as a component. Each candidate is:
    {"image": "<docker image>", "setup": ["<cmds>"], "start": "<foreground start cmd>",
     "port": <int>,
     "components": [
       {"name": "db", "role": "database", "image": "postgres:16",
        "env": {"POSTGRES_PASSWORD": "app"},
        "oracle": {"type": "native-cmd", "command": "pg_isready -U postgres"}},
       {"name": "backend", "role": "backend", "image": "python:3.11",
        "workdir": "/workspace/repo", "command": "uvicorn app:app --host 0.0.0.0 --port 8000",
        "ports": [8000], "depends_on": ["db"],
        "env": {"DATABASE_URL": "postgresql://postgres:app@db:5432/postgres"},
        "oracle": {"type": "http", "port": 8000, "path": "/health"}}
     ]}
  Wire components to each other by service NAME as host (e.g. db, redis). Give each
  component a readiness oracle describing what "ready" means for it:
    - http {port, path}            an HTTP endpoint answers
    - tcp-port {port}              a port accepts connections
    - native-cmd {command}        a command in the image succeeds (db/cache probes)
    - log-ready {pattern}         a line appears in its logs
    - process-up                  it stays running
    - exit-zero                   it runs to a clean exit (batch)
  A component with a `command` runs repo code; one without (db/cache) is a managed
  image. For a SINGLE-container service you may instead give just image/setup/start/
  port (+ optional legacy `services`/`env`), and it is treated as one component.

  NON-SERVICE repos still "succeed" by being EXERCISED, not merely by existing.
  When you can, propose a candidate whose component runs the repo to a real result
  and pick the matching oracle — success is a clean exit or expected output:
    - cli:     run a real subcommand (not just --help), oracle functional-smoke or exit-zero
    - library: run its test suite (pytest / npm test / go test), oracle tests-pass
    - batch:   run the job to completion, oracle exit-zero
    - build:   run the build/validate (make / docker build / terraform validate),
               oracle build-succeeds
  Set setup/command so the exercise actually runs (install deps first). Still
  classify honestly (cli/library/...). Only leave candidates [] when there is truly
  nothing runnable (docs-only, or you couldn't determine how to exercise it).
Output nothing else; do all reasoning via tool calls then submit_plan."""


@dataclass
class PlanResult:
    classification: str = "unknown"
    candidates: list[RunPlan] = field(default_factory=list)
    rationale: str = ""
    consulted: bool = False  # whether the model was actually reached


def _to_run_plan(candidate: dict, repo: dict, index: int) -> RunPlan | None:
    """Convert one agent candidate into a canonical RunPlan (projection to v1 is the
    graph's job, at the artifact edge). Returns None for an unrunnable candidate."""
    if not isinstance(candidate, dict):
        return None
    components = _to_run_components(candidate.get("components"))
    if not components:
        app = _single_app_component(candidate)
        if app is None:
            return None
        deps = _to_dep_components(candidate.get("services"))
        if deps:
            app.depends_on = [d.name for d in deps]
        components = [*deps, app]
    if not any(c.command for c in components):
        return None  # a system with no repo-code component is not runnable

    plan = RunPlan(
        id=f"agent_{index}",
        shape=infer_shape(components),
        confidence=confidence(["llm_inference"]),
        evidence_refs=[_AGENT_EVIDENCE_ID],
        repo=repo,
        components=components,
        source="agent",
    )
    try:
        normalize_plan(plan)  # drop candidates that violate shape/oracle/image invariants
    except ValueError:
        return None
    return plan


def _single_app_component(candidate: dict) -> RunComponent | None:
    """A single-container candidate (image/setup/start/port) as one app component;
    setup is folded into the foreground command."""
    if not (candidate.get("image") and candidate.get("start")):
        return None
    try:
        port = int(candidate.get("port", 8000))
    except (TypeError, ValueError):
        return None  # a malformed port drops this candidate, not the whole submission
    setup = [c for c in candidate.get("setup", []) if isinstance(c, str)]
    command = " && ".join([*setup, str(candidate["start"])])
    env = candidate.get("env")
    env = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
    return RunComponent(
        name="app", image=str(candidate["image"]), workdir="/workspace/repo",
        command=command, ports=[port], env=env,
        oracle=Oracle(type="http", port=port, path="/health",
                      acceptable_status=[200, 204, 301, 302, 404]),
    )


def _to_oracle(raw: object) -> Oracle | None:
    """Sanitize the agent's oracle into a valid Oracle, or None if invalid."""
    if not (isinstance(raw, dict) and raw.get("type") in ORACLE_TYPES):
        return None
    oracle = Oracle(type=str(raw["type"]))
    if isinstance(raw.get("port"), int):
        oracle.port = raw["port"]
    for key in ("path", "command", "pattern"):
        if isinstance(raw.get(key), str) and raw[key]:
            setattr(oracle, key, raw[key])
    return oracle


def _to_run_components(raw: object) -> list[RunComponent]:
    """Map the agent's component dicts into canonical RunComponents (#40).

    A component needs at least a name, an image, and a valid oracle. A component
    with a ``command`` runs repo code; one without is a managed dependency image.
    """
    if not isinstance(raw, list):
        return []
    components = []
    for item in raw:
        if not (isinstance(item, dict) and item.get("name") and item.get("image")):
            continue
        oracle = _to_oracle(item.get("oracle"))
        if oracle is None:
            continue
        components.append(
            RunComponent(
                name=str(item["name"]),
                image=str(item["image"]),
                oracle=oracle,
                role=str(item["role"]) if isinstance(item.get("role"), str) and item["role"] else None,
                workdir=str(item["workdir"]) if isinstance(item.get("workdir"), str) and item["workdir"] else None,
                command=str(item["command"]) if isinstance(item.get("command"), str) and item["command"] else None,
                ports=[p for p in (item.get("ports") or []) if isinstance(p, int)],
                env={str(k): str(v) for k, v in item["env"].items()} if isinstance(item.get("env"), dict) else {},
                depends_on=[str(d) for d in (item.get("depends_on") or []) if isinstance(d, str)],
            )
        )
    return components


def _to_dep_components(raw: object) -> list[RunComponent]:
    """Legacy agent ``services`` -> dependency components with a native-cmd oracle."""
    if not isinstance(raw, list):
        return []
    deps = []
    for item in raw:
        if not (isinstance(item, dict) and item.get("name") and item.get("image")):
            continue
        hc = item.get("healthcheck")
        deps.append(
            RunComponent(
                name=str(item["name"]), image=str(item["image"]), role="db",
                env={str(k): str(v) for k, v in item["env"].items()} if isinstance(item.get("env"), dict) else {},
                oracle=Oracle(type="native-cmd", command=hc if isinstance(hc, str) and hc else "true"),
            )
        )
    return deps


def _build_tools(repo_tools: RepoTools):
    @tool
    def list_dir(path: str = ".") -> str:
        """List the entries in a directory of the repository."""
        return json.dumps(repo_tools.list_dir(path))

    @tool
    def read_file(path: str) -> str:
        """Read a text file from the repository."""
        return repo_tools.read_file(path)

    @tool
    def search(pattern: str) -> str:
        """Grep the repository for a regex/text pattern (returns path:line: match)."""
        return json.dumps(repo_tools.search(pattern))

    @tool
    def find(glob: str) -> str:
        """Find repository files by glob (e.g. '**/Dockerfile*')."""
        return json.dumps(repo_tools.find(glob))

    @tool
    def submit_plan(classification: str, candidates: list, rationale: str = "") -> str:
        """Submit the final repo classification and ordered candidate run plans."""
        return "submitted"

    return [list_dir, read_file, search, find, submit_plan]


def explore_and_plan(chat_model: Any, repo_tools: RepoTools, seed: str, repo: dict) -> PlanResult:
    tools = _build_tools(repo_tools)
    by_name = {t.name: t for t in tools}
    model = chat_model.bind_tools(tools)

    messages: list = [SystemMessage(_SYSTEM), HumanMessage(seed)]
    for _ in range(_MAX_ITERS):
        ai: AIMessage = model.invoke(messages)
        messages.append(ai)
        calls = ai.tool_calls or []
        if not calls:
            return PlanResult(consulted=True, rationale=str(ai.content))
        for call in calls:
            if call["name"] == "submit_plan":
                args = call["args"]
                classification = args.get("classification", "unknown")
                if classification not in CLASSIFICATIONS:
                    classification = "unknown"
                raw = args.get("candidates") or []
                candidates = [
                    plan for i, c in enumerate(raw)
                    if (plan := _to_run_plan(c, repo, i)) is not None
                ]
                return PlanResult(
                    classification=classification,
                    candidates=candidates,
                    rationale=args.get("rationale", ""),
                    consulted=True,
                )
            result = by_name[call["name"]].invoke(call["args"]) if call["name"] in by_name else "(unknown tool)"
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

    return PlanResult(consulted=True, rationale="exploration budget exhausted")
