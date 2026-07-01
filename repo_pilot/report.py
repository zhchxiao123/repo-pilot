"""Report Writer (§14.6).

Renders the human-readable Markdown report: repo identification plus, when a
Runbook is present, the runtime verdict. Later slices add discovered targets and
test results.
"""

from __future__ import annotations

from repo_pilot.cloner import RepoRef


def render_report(
    repo_url: str,
    repo_ref: RepoRef,
    runbook: dict | None = None,
    deferred_reason: str | None = None,
    targets: list[dict] | None = None,
    tests: list[dict] | None = None,
) -> str:
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
        if deferred_reason:
            lines.append(f"- Status: deferred ({deferred_reason})")
        else:
            lines.append("- Status: no runnable candidate found")
        lines.append("")
        return "\n".join(lines)

    lines += ["## Runtime", ""]
    if "confidence" in runbook:
        lines.append(
            f"- Candidate: {runbook.get('id')} (confidence {runbook['confidence']:.2f})"
        )
    if runbook.get("status") == "verified":
        hc = runbook.get("verification", {}).get("healthcheck_result", {})
        lines.append("- Status: verified")
        lines.append(f"- Healthcheck: {hc.get('status_code')} at {hc.get('url')}")
        reproduce = runbook.get("verification", {}).get("reproduce", [])
        if reproduce:
            lines += ["", "### Reproduce", "", "```", *reproduce, "```"]
    else:
        lines.append("- Status: not verified (failed)")
        logs = runbook.get("verification", {}).get("logs_summary")
        if logs:
            excerpt = "\n".join(logs.strip().splitlines()[-20:])
            lines += ["", "### Logs", "", "```", excerpt, "```"]
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
