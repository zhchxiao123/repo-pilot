"""LLM-assisted profiling (Tier-B, ADR-0014/0015).

Deterministic profiling only recognizes stacks with explicit rules. When it comes
back thin (no framework, no entrypoint — an unrecognized stack), the LLM reads the
repo files and fills in structured signals: languages, frameworks, required
services, required env vars, and ports. These enrich the Profile (backed by
llm_inference evidence) so the report is richer and the LLM planner has better
input. It is analysis only — the sandbox still adjudicates whether anything runs.
"""

from __future__ import annotations

import copy
import json

from repo_pilot.model_client import ModelClient

_EVIDENCE_ID = "ev_pl1"

_PROMPT = """Characterize this repository from its files. Return ONLY a JSON object:

  {{"languages": [...], "frameworks": [...], "services": ["postgres"|"redis"|...],
    "env_required": ["DATABASE_URL", ...], "ports": [<int>, ...]}}

Base it strictly on the files shown; use empty arrays when unknown. `services` are
external dependencies the app needs (databases, caches, brokers).

## Files
{context}
"""


def _str_list(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def enrich_profile(
    profile: dict, context: str, client: ModelClient
) -> tuple[dict, list[dict]]:
    """Return (enriched_profile, evidence). Unchanged profile + [] if nothing added."""
    try:
        data = json.loads(client.complete(_PROMPT.format(context=context)))
    except json.JSONDecodeError:
        return profile, []
    if not isinstance(data, dict):
        return profile, []

    enriched = copy.deepcopy(profile)
    changed = False

    for key in ("languages", "frameworks", "services"):
        values = _str_list(data.get(key))
        if values:
            merged = sorted(set(enriched.get(key, []) + values))
            if merged != enriched.get(key, []):
                enriched[key] = merged
                changed = True

    ports = [p for p in data.get("ports", []) if isinstance(p, int)] if isinstance(data.get("ports"), list) else []
    if ports:
        enriched["ports"] = ports
        changed = True

    env_required = _str_list(data.get("env_required"))
    if env_required:
        env_vars = enriched.setdefault("env_vars", {})
        env_vars["required"] = env_required
        changed = True

    if not changed:
        return profile, []

    enriched.setdefault("evidence_refs", {})["llm_profile"] = [_EVIDENCE_ID]
    evidence = {
        "id": _EVIDENCE_ID,
        "file": "(llm)",
        "line": None,
        "kind": "llm_inference",
        "excerpt": "LLM profile enrichment (languages/frameworks/services/env/ports)",
        "reason": "deterministic profiling recognized no framework or entrypoint",
        "confidence": 0.3,
    }
    return enriched, [evidence]
