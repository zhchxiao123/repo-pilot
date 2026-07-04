"""`repo-pilot` command-line entry point.

v1 is a single-host CLI (ADR-0001). This slice wires the `run` command through to
the artifact store; the analysis pipeline is filled in by later slices.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import click

from repo_pilot.artifacts import ArtifactStore
from repo_pilot.config import load_config
from repo_pilot.eval import evaluate, format_report, load_manifest, run_case, select_cases
from repo_pilot.executor import DockerSandboxExecutor, DockerUnavailable
from repo_pilot.graph import build_graph, initial_state
from repo_pilot.model_client import build_chat_model, build_model_client
from repo_pilot.security import default_security

# provider -> the env var holding its API key (for a friendly up-front warning)
_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
}


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

    # LLM (ADR-0005/0016): the plan agent explores repos the rules don't recognize;
    # model_client backs the repair loop. Both are provider-agnostic and built from
    # config; --no-llm (or a build failure) degrades to the deterministic path only.
    model_client = None
    chat_model = None
    if not no_llm:
        try:
            model_client = build_model_client(config)
            chat_model = build_chat_model(config)
        except Exception as exc:  # missing provider package, bad config, etc.
            click.echo(f"LLM disabled ({exc}); running deterministic path only.")
        else:
            key_var = _PROVIDER_KEY_ENV.get(
                config.model.provider, f"{config.model.provider.upper()}_API_KEY"
            )
            if not (os.environ.get(key_var) or config.model.api_key):
                click.echo(
                    f"WARNING: {key_var} is not set — the plan agent (for stacks the "
                    "rules don't recognize) will be unavailable; only rule-recognized "
                    "stacks will run."
                )
    else:
        click.echo("LLM disabled (--no-llm); deterministic path only.")

    click.echo(f"Job: {job.job_id}")
    click.echo(f"Repo: {repo_url}" + (f" @ {commit}" if commit else ""))

    # Real sandbox: run the generated compose against the local Docker daemon,
    # waiting up to ~120s for the app to become healthy (ADR-0002).
    graph = build_graph(
        DockerSandboxExecutor(),
        security=security,
        model_client=model_client,
        chat_model=chat_model,
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
                compose_path=str(job.compose_path),
            )
        )
    except DockerUnavailable as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Verified: {final.get('verified', False)}")
    click.echo(f"Report: {job.report_path}")


@main.command(name="eval")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--workdir",
    default="artifacts/eval-runs",
    help="Directory to write per-case artifacts under.",
)
@click.option(
    "--threshold",
    default=0.5,
    type=float,
    help="Exit non-zero when overall coverage falls below this fraction.",
)
@click.option("--no-llm", is_flag=True, help="Disable the LLM fallback seam.")
@click.option("--limit", default=None, type=int, help="Run only the first N cases.")
@click.option("--case", "case_name", default=None, help="Run one named case.")
def eval_command(
    manifest: str,
    workdir: str,
    threshold: float,
    no_llm: bool,
    limit: int | None,
    case_name: str | None,
) -> None:
    """Sweep MANIFEST through the real pipeline and score verdict coverage.

    Each case runs isolated under --workdir/<case-name>; a crashing case records
    an ``error`` verdict without sinking the sweep (see docs/eval-harness.md).
    """
    try:
        cases = select_cases(load_manifest(manifest), limit=limit, case=case_name)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    config = load_config()
    model_client = chat_model = None
    if not no_llm:
        try:
            model_client = build_model_client(config)
            chat_model = build_chat_model(config)
        except Exception as exc:  # missing provider package, bad config, etc.
            click.echo(f"LLM disabled ({exc}); running deterministic path only.")

    def _build():
        return build_graph(
            DockerSandboxExecutor(),
            security=default_security(),
            model_client=model_client,
            chat_model=chat_model,
            healthcheck_retries=60,
            poll_interval=2.0,
        )

    # each sweep gets its own timestamped run dir; case artifacts nest inside it
    run_dir = Path(workdir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    report = evaluate(
        cases,
        lambda case: run_case(case, _build, run_dir),
        case_dir=lambda case: run_dir / case.name,
    )
    text = format_report(report)
    (run_dir / "eval-report.md").write_text(text)
    click.echo(text)
    click.echo(f"Artifacts: {run_dir}")
    if report.coverage < threshold:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
