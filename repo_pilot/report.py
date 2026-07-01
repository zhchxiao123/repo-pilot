"""Report Writer (§14.6).

Renders the human-readable Markdown report. This slice emits repo identification;
later slices add detection, the Verified Runbook, targets, and test results.
"""

from __future__ import annotations

from repo_pilot.cloner import RepoRef


def render_report(repo_url: str, repo_ref: RepoRef) -> str:
    return (
        "# repo-pilot report\n\n"
        "## Repository\n\n"
        f"- URL: {repo_url}\n"
        f"- Commit: {repo_ref.commit}\n"
        f"- Default branch: {repo_ref.default_branch}\n"
    )
