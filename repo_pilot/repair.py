"""Repair Loop (ADR-0012): diagnose a failed run and patch the canonical RunPlan.

Rules-first (fast, deterministic, §9.2), then an LLM fallback for novel failures.
A patch only ever edits the RunPlan (never the source, ADR-0003); the sandbox
still adjudicates — a patched plan is only kept if it actually verifies. Projection
back to the persisted v1 Runbook happens at the artifact boundary (the graph),
not here.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from repo_pilot.model_client import ModelClient
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, RunShape
from repo_pilot.run_verifier import RunVerification

_REPAIR_PROMPT = """A project failed to start in a container. Here is the run plan
that failed and the logs. Propose a CORRECTED plan as ONE JSON object:

  {{"image": "<image>", "setup": ["<cmds>"], "start": "<foreground start cmd>", "port": <int>}}

Only output JSON. Fix the actual cause shown in the logs.

## Failed plan
image: {image}
command: {command}

## Logs (tail)
{logs}
"""


@dataclass
class RepairProposal:
    """A proposed corrected plan plus how it was derived."""

    plan: RunPlan
    description: str
    source: str  # "rule" | "llm"


def patch_fingerprint(plan: RunPlan) -> str:
    """Stable hash of the parts a repair changes (each component's image/command/
    deps) — used to reject repeated patches."""
    payload = json.dumps(
        [
            {"name": c.name, "image": c.image, "command": c.command,
             "depends_on": sorted(c.depends_on)}
            for c in plan.components
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _primary(plan: RunPlan) -> RunComponent | None:
    for comp in plan.components:
        if comp.command:
            return comp
    return plan.components[0] if plan.components else None


def _with_primary_command(plan: RunPlan, command: str) -> RunPlan:
    patched = copy.deepcopy(plan)
    prim = _primary(patched)
    if prim is not None:
        prim.command = command
    return patched


def rule_diagnose(plan: RunPlan, logs: str) -> tuple[RunPlan, str] | None:
    """A few high-value deterministic fixes (§9.2), applied to the primary
    repo-code component's command (setup is folded into the command)."""
    prim = _primary(plan)
    command = (prim.command if prim else "") or ""
    low = logs.lower()

    if "pnpm" in low and "not found" in low and "corepack" not in command:
        return _with_primary_command(plan, f"corepack enable && {command}"), "insert corepack enable"

    if ("modulenotfounderror" in low or "no module named" in low) and "pip install" not in command:
        return _with_primary_command(plan, f"pip install -r requirements.txt && {command}"), "add pip install"

    if "npm" in low and ("enoent" in low or "cannot find module" in low) and "npm install" not in command:
        return _with_primary_command(plan, f"npm install && {command}"), "add npm install"

    db_signals = ("could not connect", "connection refused", "econnrefused",
                  "could not translate host name", "psycopg", "getaddrinfo")
    has_db = any(c.name == "postgres" for c in plan.components)
    if any(s in low for s in db_signals) and not has_db:
        patched = copy.deepcopy(plan)
        patched.components.append(
            RunComponent(
                name="postgres",
                image="postgres:16",
                role="db",
                env={"POSTGRES_USER": "app", "POSTGRES_PASSWORD": "app", "POSTGRES_DB": "app"},
                oracle=Oracle(type="native-cmd", command="pg_isready -U app"),
            )
        )
        patched.shape = RunShape.MULTI_COMPONENT_SERVICE
        prim = _primary(patched)
        if prim is not None:
            prim.env.setdefault("DATABASE_URL", "postgresql://app:app@postgres:5432/app")
            if "postgres" not in prim.depends_on:
                prim.depends_on.append("postgres")
        return patched, "provision postgres service"

    return None


def _llm_repair(plan: RunPlan, logs: str, client: ModelClient) -> tuple[RunPlan, str] | None:
    prim = _primary(plan)
    if prim is None:
        return None
    prompt = _REPAIR_PROMPT.format(
        image=prim.image or "", command=prim.command or "", logs=logs[-2000:]
    )
    try:
        item = json.loads(client.complete(prompt))
    except json.JSONDecodeError:
        return None
    if not (isinstance(item, dict) and item.get("start")):
        return None

    setup = [c for c in item.get("setup", []) if isinstance(c, str)]
    command = " && ".join([*setup, str(item["start"])])
    patched = copy.deepcopy(plan)
    new_prim = _primary(patched)
    assert new_prim is not None
    if item.get("image"):
        new_prim.image = str(item["image"])
    new_prim.command = command
    if isinstance(item.get("port"), int):
        new_prim.ports = [item["port"]]
        if new_prim.oracle is not None and new_prim.oracle.type == "http":
            new_prim.oracle.port = item["port"]
    return patched, "llm repair"


def propose_repair(
    plan: RunPlan, failure: RunVerification | str, client: ModelClient | None
) -> RepairProposal | None:
    """Return a RepairProposal or None. Rules first, then LLM. ``failure`` may be a
    RunVerification (its log tail is used) or a raw log string."""
    logs = failure.logs_summary if isinstance(failure, RunVerification) else failure
    result = rule_diagnose(plan, logs)
    source = "rule"
    if result is None and client is not None:
        result = _llm_repair(plan, logs, client)
        source = "llm"
    if result is None:
        return None
    patched, description = result
    return RepairProposal(plan=patched, description=description, source=source)
