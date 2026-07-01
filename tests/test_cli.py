"""The `repo-pilot run` command parses args and creates a per-job artifact dir."""

from click.testing import CliRunner

from repo_pilot.cli import main


def test_run_creates_a_per_job_artifact_directory(tmp_path):
    result = CliRunner().invoke(
        main,
        ["run", "https://github.com/org/repo", "--artifacts-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    jobs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(jobs) == 1
    # the created job id is reported to the operator
    assert jobs[0].name in result.output


def test_run_accepts_a_commit_option(tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "run",
            "https://github.com/org/repo",
            "--commit",
            "abc123",
            "--artifacts-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
