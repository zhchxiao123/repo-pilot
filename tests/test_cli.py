"""`repo-pilot run` clones a repo and writes a report into a per-job artifact dir."""

from click.testing import CliRunner

from repo_pilot.cli import main


def test_run_clones_and_writes_a_report(tmp_path, git_origin):
    origin, _first, second = git_origin
    art = tmp_path / "art"
    result = CliRunner().invoke(
        main, ["run", str(origin), "--artifacts-root", str(art)]
    )
    assert result.exit_code == 0, result.output

    jobs = [p for p in art.iterdir() if p.is_dir()]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.name in result.output  # job id reported to the operator

    report = (job / "report.md").read_text()
    assert str(origin) in report
    assert second in report  # default branch HEAD was cloned


def test_run_checks_out_the_requested_commit(tmp_path, git_origin):
    origin, first, _second = git_origin
    art = tmp_path / "art"
    result = CliRunner().invoke(
        main,
        ["run", str(origin), "--commit", first, "--artifacts-root", str(art)],
    )
    assert result.exit_code == 0, result.output

    job = [p for p in art.iterdir() if p.is_dir()][0]
    assert first in (job / "report.md").read_text()
    assert (job / "repo" / "VERSION").read_text() == "one\n"
