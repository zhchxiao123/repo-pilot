"""Report Writer (§14.6).

Renders the human-readable Markdown report: repo identification plus, when a
Runbook is present, the runtime verdict. Later slices add discovered targets and
test results.
"""

from __future__ import annotations

from repo_pilot.cloner import RepoRef
from repo_pilot.outcome import outcome_from_state


def _outcome_of(
    runbook: dict | None, deferred_reason: str | None, classification: str | None
):
    """The terminal Outcome for this report, via the shared taxonomy so the report
    agrees with eval on what happened."""
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
) -> str:
    outcome = _outcome_of(runbook, deferred_reason, classification)
    lines = [
        "# repo-pilot report",
        "",
        "## Repository",
        "",
        f"- URL: {repo_url}",
        f"- Commit: {repo_ref.commit}",
        f"- Default branch: {repo_ref.default_branch}",
        "",
    ]

    if runbook is None:
        lines += ["## Runtime", ""]
        lines.append(f"- Outcome: {outcome.verdict()}")
        if classification:
            lines.append(f"- Classification: {classification}")
        if deferred_reason and deferred_reason.startswith("not-a-service:"):
            kind = deferred_reason.split(":", 1)[1]
            lines.append(f"- Startup method: none — this is a {kind} repo, not a runnable service")
        elif deferred_reason:
            lines.append(f"- Status: deferred ({deferred_reason})")
        else:
            lines.append("- Status: no runnable candidate found")
        lines.append("")
        return "\n".join(lines)

    lines += ["## Runtime", ""]
    lines.append(f"- Outcome: {outcome.verdict()}")
    if classification:
        lines.append(f"- Classification: {classification}")
    if "confidence" in runbook:
        lines.append(
            f"- Candidate: {runbook.get('id')} (confidence {runbook['confidence']:.2f})"
        )
    verification = runbook.get("verification", {})
    components = verification.get("components")
    if runbook.get("status") == "verified":
        lines.append("- Status: verified")
        if components:
            # a system of components: report each component's oracle verdict
            lines += ["", "### Components", ""]
            for c in components:
                lines.append(f"- {c['name']}: {c['oracle']} — reached ({c['detail']})")
        else:
            hc = verification.get("healthcheck_result", {})
            lines.append(f"- Healthcheck: {hc.get('status_code')} at {hc.get('url')}")
        reproduce = verification.get("reproduce", [])
        if reproduce:
            lines += ["", "### Reproduce", "", "```", *reproduce, "```"]
    else:
        lines.append("- Status: not verified (failed)")
        if components:
            lines += ["", "### Components", ""]
            for c in components:
                verdict = "reached" if c["passed"] else "NOT reached"
                lines.append(f"- {c['name']}: {c['oracle']} — {verdict} ({c['detail']})")
        logs = runbook.get("verification", {}).get("logs_summary")
        if logs:
            excerpt = "\n".join(logs.strip().splitlines()[-20:])
            lines += ["", "### Logs", "", "```", excerpt, "```"]
    lines.append("")

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
