"""Healthcheck logic (§24.2).

A deterministic tool: probe the running sandbox's published ports across the
candidate paths in order and accept the first response with an acceptable status.
For a real container the app may boot slowly, so probing can be retried up to
``retries`` times with ``poll_interval`` between passes. Truth comes from the
probe, never from an agent's assertion (ADR-0004).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from repo_pilot.executor import RunningSandbox

DEFAULT_URL_CANDIDATES = ["/health", "/api/health", "/ready", "/docs", "/openapi.json", "/"]
DEFAULT_ACCEPTABLE_STATUS = [200, 301, 302, 404]

# Per-request probe timeout (§24.2). Distinct from the runbook's
# healthcheck.timeout_seconds, which is the overall wait budget.
PER_REQUEST_TIMEOUT = 3.0


@dataclass(frozen=True)
class HealthcheckResult:
    passed: bool
    url: str | None = None
    status_code: int | None = None


def _probe_once(sandbox: RunningSandbox, spec: dict) -> HealthcheckResult:
    paths = spec.get("url_candidates") or DEFAULT_URL_CANDIDATES
    acceptable = spec.get("acceptable_status") or DEFAULT_ACCEPTABLE_STATUS

    for host_port in sandbox.ports.values():
        for path in paths:
            status = sandbox.http_get(host_port, path, timeout=PER_REQUEST_TIMEOUT)
            if status is not None and status in acceptable:
                return HealthcheckResult(
                    passed=True,
                    url=f"http://127.0.0.1:{host_port}{path}",
                    status_code=status,
                )
    return HealthcheckResult(passed=False)


def run_healthcheck(
    sandbox: RunningSandbox,
    spec: dict,
    *,
    retries: int = 0,
    poll_interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> HealthcheckResult:
    for attempt in range(retries + 1):
        result = _probe_once(sandbox, spec)
        if result.passed:
            return result
        if attempt < retries:
            sleep(poll_interval)
    return HealthcheckResult(passed=False)
