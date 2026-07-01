"""NL-prose run-intent extraction (Tier-B seam, ADR-0004).

Fires only when deterministic extraction found no run command. Asks the model for a
schema-constrained JSON array of shell commands; anything unparseable or non-string
is dropped. The proposal is subordinate to the sandbox — a candidate built from it
is only trusted if it actually verifies.
"""

from __future__ import annotations

import json

from repo_pilot.model_client import ModelClient

_PROMPT = """You are given a project's README. Return ONLY a JSON array of the shell
commands needed to install and start this project locally, in order. No prose.

README:
{readme}
"""


def nl_extract_commands(readme_text: str, client: ModelClient) -> list[str]:
    response = client.complete(_PROMPT.format(readme=readme_text))
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]
