"""`repo-pilot run` wiring.

The success path runs real Docker (integration). The fast test covers arg parsing,
job-dir creation, and the friendly error when Docker is unavailable.
"""

import pytest
from click.testing import CliRunner

from repo_pilot.cli import main


def test_run_creates_job_and_surfaces_docker_unavailable(
    tmp_path, git_repo_from, fixture_repo, monkeypatch
):
    # an Express repo plans a real candidate, so verify reaches the executor
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    art = tmp_path / "art"
    # point the executor at a non-existent compose command -> DockerUnavailable
    monkeypatch.setenv("REPO_PILOT_COMPOSE_CMD", "repo-pilot-no-such-docker")

    result = CliRunner().invoke(
        main, ["run", str(origin), "--artifacts-root", str(art)]
    )

    assert result.exit_code != 0
    assert "not found" in result.output.lower()
    # arg parsing + job dir creation happened before the sandbox step
    jobs = [p for p in art.iterdir() if p.is_dir()]
    assert len(jobs) == 1


@pytest.mark.integration
def test_run_end_to_end_clones_verifies_and_reports(tmp_path, git_origin):
    import shutil

    if shutil.which("docker") is None:
        pytest.skip("docker not available")

    origin, _first, second = git_origin
    art = tmp_path / "art"
    result = CliRunner().invoke(
        main, ["run", str(origin), "--artifacts-root", str(art)]
    )
    assert result.exit_code == 0, result.output
    job = [p for p in art.iterdir() if p.is_dir()][0]
    report = (job / "report.md").read_text()
    assert str(origin) in report
    assert second in report
