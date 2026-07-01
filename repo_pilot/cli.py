"""`repo-pilot` command-line entry point.

v1 is a single-host CLI (ADR-0001). This slice wires the `run` command through to
the artifact store; the analysis pipeline is filled in by later slices.
"""

from __future__ import annotations

import click

from repo_pilot.artifacts import ArtifactStore
from repo_pilot.config import load_config
from repo_pilot.executor import DockerSandboxExecutor, DockerUnavailable
from repo_pilot.graph import build_graph, initial_state
from repo_pilot.model_client import build_model_client
from repo_pilot.security import default_security


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
@click.option("--allow-private-egress", is_flag=True, help="Allow egress to private networks.")
@click.option("--allow-metadata", is_flag=True, help="Allow egress to the cloud metadata endpoint.")
@click.option("--no-isolation", is_flag=True, help="Disable network isolation entirely.")
@click.option("--no-llm", is_flag=True, help="Disable the LLM fallback seam.")
def run(
    repo_url: str,
    commit: str | None,
    artifacts_root: str | None,
    allow_private_egress: bool,
    allow_metadata: bool,
    no_isolation: bool,
    no_llm: bool,
) -> None:
    """Analyze, verify, and test REPO_URL, writing artifacts for the job."""
    config = load_config()
    root = artifacts_root or config.artifacts_root
    job = ArtifactStore(root).create_job()

    # Default-safe security envelope; opt-out via flags (ADR-0007).
    security = default_security()
    if allow_private_egress:
        security["egress"] = "allow_private"
    if allow_metadata:
        security["allow_metadata"] = True
    if no_isolation:
        security["isolation"] = False

    # Provider-agnostic LLM fallback (ADR-0005), gated: only fires when
    # deterministic planning finds nothing. Degrades to deterministic-only if the
    # provider backend can't be built (missing package/config).
    model_client = None
    if not no_llm:
        try:
            model_client = build_model_client(config)
        except Exception as exc:  # missing provider package, bad config, etc.
            click.echo(f"LLM fallback disabled ({exc}); continuing deterministically.")

    click.echo(f"Job: {job.job_id}")
    click.echo(f"Repo: {repo_url}" + (f" @ {commit}" if commit else ""))

    # Real sandbox: run the generated compose against the local Docker daemon,
    # waiting up to ~120s for the app to become healthy (ADR-0002).
    graph = build_graph(
        DockerSandboxExecutor(),
        security=security,
        model_client=model_client,
        healthcheck_retries=60,
        poll_interval=2.0,
    )
    try:
        final = graph.invoke(
            initial_state(
                repo_url=repo_url,
                commit=commit,
                repo_dir=str(job.dir / "repo"),
                report_path=str(job.report_path),
                runbook_path=str(job.runbook_path),
                profile_path=str(job.profile_path),
                evidence_path=str(job.evidence_path),
            )
        )
    except DockerUnavailable as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Verified: {final.get('verified', False)}")
    click.echo(f"Report: {job.report_path}")


if __name__ == "__main__":
    main()
