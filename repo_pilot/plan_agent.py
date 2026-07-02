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
from repo_pilot.planner import default_healthcheck
from repo_pilot.schemas import SchemaValidationError, validate_runbook

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
- candidates: for a `service`, 1-3 ordered run plans, best first, each:
    {"image": "<docker image>", "setup": ["<cmds>"], "start": "<foreground start cmd>",
     "port": <int>,
     "services": [{"name": "postgres", "image": "postgres:16",
                   "env": {"POSTGRES_PASSWORD": "app"}, "healthcheck": "pg_isready -U postgres"}],
     "env": {"DATABASE_URL": "postgresql://postgres:app@postgres:5432/postgres"}}
  Include `services` for external deps the app needs (db/cache/broker) and `env`
  for the vars it requires (reference services by name as host). Omit if none.
  For non-services, candidates is [].
Output nothing else; do all reasoning via tool calls then submit_plan."""


@dataclass
class PlanResult:
    classification: str = "unknown"
    candidates: list[dict] = field(default_factory=list)
    rationale: str = ""
    consulted: bool = False  # whether the model was actually reached


def _to_runbook(candidate: dict, repo: dict, index: int) -> dict | None:
    if not (isinstance(candidate, dict) and candidate.get("image") and candidate.get("start")):
        return None
    try:
        port = int(candidate.get("port", 8000))
    except (TypeError, ValueError):
        return None  # a malformed port drops this candidate, not the whole submission
    setup = [{"command": c} for c in candidate.get("setup", []) if isinstance(c, str)]
    runbook = {
        "schema_version": "v1",
        "id": f"agent_{index}",
        "status": "candidate",
        "confidence": confidence(["llm_inference"]),
        "evidence_refs": [_AGENT_EVIDENCE_ID],
        "repo": repo,
        "runtime": {
            "image": str(candidate["image"]),
            "workdir": "/workspace/repo",
            "resources": {"cpu": 2, "memory": "4g", "pids": 512, "timeout_seconds": 900},
        },
        "steps": {
            "setup": setup,
            "start": [{"command": str(candidate["start"]), "expected_ports": [port]}],
        },
        "healthcheck": default_healthcheck(),
    }

    services = _to_services(candidate.get("services"))
    if services:
        runbook["services"] = services
    env = candidate.get("env")
    if isinstance(env, dict) and env:
        runbook["env"] = {"generated": {str(k): str(v) for k, v in env.items()}}

    try:
        validate_runbook(runbook)
    except SchemaValidationError:
        return None
    return runbook


def _to_services(raw: object) -> list[dict]:
    """Map the agent's service dicts into schema-valid Runbook service specs."""
    if not isinstance(raw, list):
        return []
    services = []
    for item in raw:
        if not (isinstance(item, dict) and item.get("name") and item.get("image")):
            continue
        svc: dict = {"name": str(item["name"]), "image": str(item["image"])}
        if isinstance(item.get("env"), dict):
            svc["env"] = {str(k): str(v) for k, v in item["env"].items()}
        hc = item.get("healthcheck")
        if isinstance(hc, str) and hc:
            svc["healthcheck"] = {"type": "command", "command": hc}
        services.append(svc)
    return services


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
                    rb for i, c in enumerate(raw)
                    if (rb := _to_runbook(c, repo, i)) is not None
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
