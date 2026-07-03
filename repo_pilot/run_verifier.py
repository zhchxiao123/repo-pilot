"""One interface for verifying a canonical ``RunPlan`` (Task 3).

``verify_run_plan`` is the single entry point callers use to adjudicate a run
plan against a sandbox. It hides single-app vs multi-component verification: a
plan is verified when *every* component reaches its oracle. Callers get back
facts (a ``RunVerification``), not a mutated runbook dict — projection back to
the persisted artifact is the caller's concern (``runbook_projection``).

LLM proposes, sandbox adjudicates (ADR-0004): success comes only from oracle
execution here, never from an assertion in the plan.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from repo_pilot.component_oracles import verify_component
from repo_pilot.compose import compile_components
from repo_pilot.executor import RunningSandbox, SandboxExecutor
from repo_pilot.run_shape import RunPlan, normalize_plan
from repo_pilot.runbook_projection import plan_to_components
from repo_pilot.security import redact


@dataclass(frozen=True)
class ComponentVerification:
    """The adjudicated result of one component's readiness oracle."""

    name: str
    oracle: str
    passed: bool
    detail: str = ""


@dataclass
class RunVerification:
    """Facts from verifying a run plan against a sandbox.

    ``sandbox`` is the live sandbox on success (kept up so discover/test can use
    it) and ``None`` on failure (already stopped). ``ports`` is the flattened
    published-port list; ``compose`` is the compiled project for provenance.
    """

    verified: bool
    logs_summary: str
    ports: list[dict] = field(default_factory=list)
    component_results: list[ComponentVerification] = field(default_factory=list)
    sandbox: RunningSandbox | None = None
    compose: dict = field(default_factory=dict)


def verify_run_plan(
    plan: RunPlan,
    executor: SandboxExecutor,
    repo_dir: str,
    *,
    retries: int = 0,
    poll_interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> RunVerification:
    """Bring ``plan`` up in a sandbox and adjudicate every component's oracle.

    Starts the sandbox once, verifies each component post-up (compose already
    gates start order for native-cmd dependencies), and returns a
    ``RunVerification``. Stops the sandbox on failure; leaves it running on
    success for downstream HTTP discovery/smoke.
    """
    normalize_plan(plan)  # enforce shape/oracle/image invariants before starting
    components = plan_to_components(plan)
    compose = compile_components(components)
    sandbox = executor.start(compose, repo_dir=repo_dir)

    results: list[ComponentVerification] = []
    for comp in components:
        res = verify_component(
            comp, sandbox, retries=retries, poll_interval=poll_interval, sleep=sleep
        )
        results.append(
            ComponentVerification(
                name=comp["name"],
                oracle=comp["oracle"]["type"],
                passed=res.passed,
                detail=res.detail,
            )
        )

    verified = all(r.passed for r in results)
    logs = redact(sandbox.logs)  # scrub secrets before anything is stored (§20.2)
    ports = [
        {"container": c, "host": h}
        for comp in components
        for c, h in sandbox.service_ports(comp["name"]).items()
    ]

    if verified:
        return RunVerification(
            verified=True,
            logs_summary=logs,
            ports=ports,
            component_results=results,
            sandbox=sandbox,
            compose=compose,
        )
    sandbox.stop()
    return RunVerification(
        verified=False,
        logs_summary=logs,
        ports=ports,
        component_results=results,
        sandbox=None,
        compose=compose,
    )
