"""`repo-pilot` command-line entry point.

v1 is a single-host CLI (ADR-0001). This slice wires the `run` command through to
the artifact store; the analysis pipeline is filled in by later slices.
"""

from __future__ import annotations

import click

from repo_pilot.artifacts import ArtifactStore
from repo_pilot.config import load_config


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
    click.echo(f"Artifacts: {job.dir}")
    click.echo(f"Repo: {repo_url}" + (f" @ {commit}" if commit else ""))


if __name__ == "__main__":
    main()
