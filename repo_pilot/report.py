"""Report Writer (§14.6).

Renders the human-readable Markdown report around *how the repo ran*: its
terminal Outcome (shape-specific verdict), the Run Plan that was exercised, the
per-component oracle verdicts, and shape-appropriate reproduce steps. Renders from
the shared Outcome taxonomy and the canonical RunPlan; the legacy ``runbook`` dict
is accepted as compatibility input and converted with ``runbook_to_plan``.
"""

from __future__ import annotations

from repo_pilot.cloner import RepoRef
from repo_pilot.outcome import outcome_from_state
from repo_pilot.run_shape import NormalizedRunPlan
from repo_pilot.runbook_projection import runbook_to_plan


def _outcome_of(
    runbook: dict | None, deferred_reason: str | None, classification: str | None
):
    return outcome_from_state(
        {
            "verified": bool(runbook and runbook.get("status") == "verified"),
            "classification": classification,
            "deferred_reason": deferred_reason,
            "runbook": runbook,
        }
    )


def render_report(
    repo_url: str,
    repo_ref: RepoRef,
    runbook: dict | None = None,
    deferred_reason: str | None = None,
    classification: str | None = None,
    targets: list[dict] | None = None,
    tests: list[dict] | None = None,
    compose_artifact: str | None = None,
) -> str:
    outcome = _outcome_of(runbook, deferred_reason, classification)
    plan = NormalizedRunPlan(runbook_to_plan(runbook)) if runbook is not None else None

    lines = [
        "# repo-pilot report",
        "",
        "## Repository",
        "",
        f"- URL: {repo_url}",
        f"- Commit: {repo_ref.commit}",
        f"- Default branch: {repo_ref.default_branch}",
        "",
        "## Outcome",
        "",
        f"- Verdict: {outcome.kind.value}",
        f"- Shape: {outcome.shape}",
    ]

    if outcome.verified and plan is not None:
        exercised = sorted({c.oracle.type for c in plan.plan.components if c.oracle})
        if exercised:
            lines.append(f"- Exercised by: {', '.join(exercised)}")
    elif not outcome.runnable:
        lines.append(f"- Reason: {outcome.summary}")
    elif outcome.detail:
        lines.append(f"- Reason: {outcome.detail}")
    lines.append("")

    if classification:
        lines.append(f"- Classification: {classification}")
        lines.append("")

    if runbook is None:
        # Nothing was run (docs / no candidate / deferred). Say why, plainly.
        if deferred_reason and deferred_reason.startswith("not-a-service:"):
            kind = deferred_reason.split(":", 1)[1]
            lines.append(f"- Startup method: none — this is a {kind} repo, not a runnable service")
        elif deferred_reason:
            lines.append(f"- Status: deferred ({deferred_reason})")
        else:
            lines.append("- Status: no runnable candidate found")
        lines.append("")
        return "\n".join(lines)

    # Run Plan: the components that were (or would be) exercised.
    assert plan is not None
    lines += ["## Run Plan", ""]
    if "confidence" in runbook:
        lines.append(f"- Candidate: {runbook.get('id')} (confidence {runbook['confidence']:.2f})")
    for comp in plan.plan.components:
        lines.append(f"- {comp.name}: {comp.image}")
        if comp.command:
            lines.append(f"  - command: {comp.command}")
        if comp.oracle is not None:
            lines.append(f"  - oracle: {comp.oracle.type}")
    lines.append("")

    verification = runbook.get("verification", {})
    components = verification.get("components")
    if components:
        lines += ["### Components", ""]
        for c in components:
            verdict = "reached" if c["passed"] else "NOT reached"
            lines.append(f"- {c['name']}: {c['oracle']} — {verdict} ({c['detail']})")
        lines.append("")

    if outcome.verified:
        multi_component = len(plan.plan.components) > 1
        if multi_component and compose_artifact:
            # The generated stack is persisted beside this report; bring it up from
            # there (dependency components have no command of their own).
            reproduce = [
                f"git clone {repo_url} repo",
                f"# the generated component stack is saved as {compose_artifact} in this run's artifacts",
                f"docker compose -f {compose_artifact} up",
            ]
        else:
            reproduce = ["git clone " + repo_url + " repo", "cd repo", *plan.reproduce_commands()]
        lines += ["## Reproduce", "", "```", *reproduce, "```", ""]
    else:
        logs = verification.get("logs_summary")
        if logs:
            excerpt = "\n".join(logs.strip().splitlines()[-20:])
            lines += ["### Logs", "", "```", excerpt, "```", ""]

    repairs = runbook.get("repair_history") or []
    if repairs:
        lines += ["## Repair attempts", ""]
        for r in repairs:
            lines.append(f"- attempt {r['attempt']} ({r['source']}): {r['diagnosis']}")
        lines.append("")

    if targets:
        lines += ["## Test targets", ""]
        for t in targets:
            lines.append(f"- {t['method']} {t['path']} ({t['source']})")
        lines.append("")

    if tests:
        passed = sum(1 for t in tests if t["status"] == "passed")
        lines += ["## Smoke tests", "", f"- {passed}/{len(tests)} passed", ""]
        for t in tests:
            if t["status"] == "failed":
                req = t["request"]
                lines.append(f"- FAIL {req['method']} {req['url']} — {t['reason']}")
                lines.append(f"  reproduce: {t['reproduce'][0]}")
        lines.append("")

    return "\n".join(lines)
