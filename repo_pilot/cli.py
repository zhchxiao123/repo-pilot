"""`repo-pilot` command-line entry point.

v1 is a single-host CLI (ADR-0001). This slice wires the `run` command through to
the artifact store; the analysis pipeline is filled in by later slices.
"""

from __future__ import annotations

import click

from repo_pilot.artifacts import ArtifactStore
from repo_pilot.config import load_config
from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.graph import build_graph, initial_state


@click.group()
@click.version_option()
def main() -> None:
    """Turn a GitHub repo into a Verified Runbook plus a smoke-test report."""


@main.command()
@click.argument("repo_url")
@click.option("--commit", default=None, help="Commit SHA to pin the analysis to.")
@click.option(
    "--artifacts-root",
    default=None,
    help="Directory to write per-job artifacts under (overrides config).",
)
def run(repo_url: str, commit: str | None, artifacts_root: str | None) -> None:
    """Analyze, verify, and test REPO_URL, writing artifacts for the job."""
    config = load_config()
    root = artifacts_root or config.artifacts_root
    job = ArtifactStore(root).create_job()

    click.echo(f"Job: {job.job_id}")
    click.echo(f"Repo: {repo_url}" + (f" @ {commit}" if commit else ""))

    # Placeholder executor until the real Docker executor lands (slice 4).
    # Verification is simulated; the report notes this is not a real run yet.
    executor = FakeSandboxExecutor(
        ports={3000: 3000}, responses={"/health": 200, "/": 200}
    )
    graph = build_graph(executor)
    graph.invoke(
        initial_state(
            repo_url=repo_url,
            commit=commit,
            repo_dir=str(job.dir / "repo"),
            report_path=str(job.report_path),
            runbook_path=str(job.runbook_path),
        )
    )

    click.echo("Verification: simulated (fake executor — real Docker lands in slice 4)")
    click.echo(f"Report: {job.report_path}")


if __name__ == "__main__":
    main()
