"""Repair Loop (ADR-0012): diagnose a failed start and patch the Runbook.

Rules-first (fast, deterministic, §9.2), then an LLM fallback for novel failures.
A patch only ever edits the Runbook (never the source, ADR-0003); it is rejected if
it would weaken the security envelope (ADR-0007); the sandbox still adjudicates —
a patched Runbook is only kept if it actually verifies.
"""

from __future__ import annotations

import copy
import hashlib
import json

from repo_pilot.model_client import ModelClient
from repo_pilot.schemas import SchemaValidationError, validate_runbook

_REPAIR_PROMPT = """A project failed to start in a container. Here is the run plan
that failed and the logs. Propose a CORRECTED plan as ONE JSON object:

  {{"image": "<image>", "setup": ["<cmds>"], "start": "<foreground start cmd>", "port": <int>}}

Only output JSON. Fix the actual cause shown in the logs.

## Failed plan
image: {image}
setup: {setup}
start: {start}

## Logs (tail)
{logs}
"""


def patch_fingerprint(runbook: dict) -> str:
    """Stable hash of the parts a repair changes — used to reject repeated patches."""
    payload = json.dumps(
        {"runtime": runbook.get("runtime"), "steps": runbook.get("steps")},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _setup_commands(runbook: dict) -> list[str]:
    return [s["command"] for s in runbook.get("steps", {}).get("setup", [])]


def _insert_setup(runbook: dict, command: str, *, front: bool = False) -> dict:
    patched = copy.deepcopy(runbook)
    steps = patched.setdefault("steps", {})
    setup = steps.setdefault("setup", [])
    step = {"command": command}
    setup.insert(0, step) if front else setup.append(step)
    return patched


def rule_diagnose(runbook: dict, logs: str) -> tuple[dict, str] | None:
    """A few high-value deterministic fixes (§9.2)."""
    setup = " ".join(_setup_commands(runbook))
    low = logs.lower()

    if "pnpm" in low and "not found" in low and "corepack" not in setup:
        return _insert_setup(runbook, "corepack enable", front=True), "insert corepack enable"

    if ("modulenotfounderror" in low or "no module named" in low) and "pip install" not in setup:
        return _insert_setup(runbook, "pip install -r requirements.txt"), "add pip install"

    if "npm" in low and ("enoent" in low or "cannot find module" in low) and "npm install" not in setup:
        return _insert_setup(runbook, "npm install", front=True), "add npm install"

    return None


def _llm_repair(runbook: dict, logs: str, client: ModelClient) -> tuple[dict, str] | None:
    start = runbook.get("steps", {}).get("start", [{}])[0]
    prompt = _REPAIR_PROMPT.format(
        image=runbook.get("runtime", {}).get("image", ""),
        setup=json.dumps(_setup_commands(runbook)),
        start=start.get("command", ""),
        logs=logs[-2000:],
    )
    try:
        item = json.loads(client.complete(prompt))
    except json.JSONDecodeError:
        return None
    if not (isinstance(item, dict) and item.get("start")):
        return None

    patched = copy.deepcopy(runbook)
    if item.get("image"):
        patched["runtime"]["image"] = str(item["image"])
    patched["steps"]["setup"] = [
        {"command": c} for c in item.get("setup", []) if isinstance(c, str)
    ]
    port = int(item.get("port", start.get("expected_ports", [8000])[0]))
    patched["steps"]["start"] = [{"command": str(item["start"]), "expected_ports": [port]}]
    return patched, "llm repair"


def propose_repair(
    runbook: dict, logs: str, client: ModelClient | None
) -> tuple[dict, str, str] | None:
    """Return (patched_runbook, description, source) or None. Rules first, then LLM."""
    result = rule_diagnose(runbook, logs)
    source = "rule"
    if result is None and client is not None:
        result = _llm_repair(runbook, logs, client)
        source = "llm"
    if result is None:
        return None

    patched, description = result
    # never trade away the security envelope; keep the original's if present
    if "security" in runbook:
        patched["security"] = runbook["security"]
    patched["status"] = "candidate"
    try:
        validate_runbook(patched)
    except SchemaValidationError:
        return None
    return patched, description, source
