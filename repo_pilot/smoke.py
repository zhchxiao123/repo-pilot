"""Weak-oracle smoke tests (§11.2).

Generate one GET smoke test per discovered Test Target (bound to it, §11.4) and run
them against the live app. Weak oracle: a target must not 5xx, not be unreachable,
and not leak a stack trace. Each failure carries the request, response excerpt, and
a reproduce command.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from repo_pilot.executor import RunningSandbox

_STACK_MARKERS = ("Traceback (most recent call last)", "\n    at ", "\nException")
_SECRET_MARKERS = (
    "BEGIN RSA PRIVATE KEY",
    "BEGIN PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY",
    "aws_secret_access_key",
)


def generate_smoke_tests(targets: list[dict]) -> list[dict]:
    tests = []
    for target in targets:
        tests.append(
            {
                "name": f"smoke GET {target['path']}",
                "method": "GET",
                "base_url": target["base_url"],
                "path": target["path"],
                "target": target,
            }
        )
    return tests


def _leaks_stack_trace(body: str | None) -> bool:
    if not body:
        return False
    return any(marker in body for marker in _STACK_MARKERS)


def _leaks_secret(body: str | None) -> bool:
    if not body:
        return False
    return any(marker in body for marker in _SECRET_MARKERS)


def _is_invalid_json(body: str | None) -> bool:
    """True only if the body looks like JSON but fails to parse (§11.2 item 3)."""
    if not body:
        return False
    if body.lstrip()[:1] not in ("{", "["):
        return False
    try:
        json.loads(body)
        return False
    except json.JSONDecodeError:
        return True


def run_smoke_tests(sandbox: RunningSandbox, tests: list[dict]) -> list[dict]:
    results = []
    for test in tests:
        url = f"{test['base_url']}{test['path']}"
        host_port = urlsplit(test["base_url"]).port
        status, body = sandbox.fetch(host_port, test["path"])

        if status is None:
            passed, reason = False, "unreachable"
        elif status >= 500:
            passed, reason = False, f"returned {status}"
        elif _leaks_stack_trace(body):
            passed, reason = False, "response leaked a stack trace"
        elif _leaks_secret(body):
            passed, reason = False, "response leaked a secret"
        elif _is_invalid_json(body):
            passed, reason = False, "response is not valid JSON"
        else:
            passed, reason = True, "ok"

        results.append(
            {
                "name": test["name"],
                "status": "passed" if passed else "failed",
                "reason": reason,
                "request": {"method": test["method"], "url": url},
                "response": {
                    "status": status,
                    "body_excerpt": (body or "")[:200],
                },
                "reproduce": [f"curl -i {url}"],
                "target": test["target"],
            }
        )
    return results
