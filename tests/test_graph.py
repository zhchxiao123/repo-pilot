"""The macro-skeleton graph runs every phase in order and reaches a verdict.

The Sandbox Executor is injected (ADR-0004 seam) so the whole pipeline runs with
no Docker.
"""

import yaml

from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.graph import MACRO_PHASES, build_graph, initial_state
from repo_pilot.schemas import validate_runbook


def _run(executor, tmp_path, origin):
    graph = build_graph(executor)
    return graph.invoke(
        initial_state(
            repo_url=str(origin),
            commit=None,
            repo_dir=str(tmp_path / "work" / "repo"),
            report_path=str(tmp_path / "report.md"),
            runbook_path=str(tmp_path / "runbook.yaml"),
        )
    )


def _success_executor():
    return FakeSandboxExecutor(ports={3000: 49152}, responses={"/": 200, "/health": 200})


def test_graph_runs_all_phases_in_order_clones_and_reports(tmp_path, git_origin):
    origin, _first, second = git_origin
    final = _run(_success_executor(), tmp_path, origin)

    assert final["visited"] == MACRO_PHASES
    assert final["repo_ref"].commit == second
    report = (tmp_path / "report.md").read_text()
    assert str(origin) in report
    assert second in report


def test_verify_pass_marks_runbook_verified(tmp_path, git_origin):
    origin, _first, _second = git_origin
    final = _run(_success_executor(), tmp_path, origin)

    assert final["verified"] is True
    assert final["runbook"]["status"] == "verified"
    assert final["runbook"]["verification"]["healthcheck_result"]["passed"] is True
    assert "verified" in (tmp_path / "report.md").read_text().lower()


def test_verified_runbook_is_persisted_and_schema_valid(tmp_path, git_origin):
    origin, _first, _second = git_origin
    _run(_success_executor(), tmp_path, origin)

    runbook_file = tmp_path / "runbook.yaml"
    assert runbook_file.is_file()
    data = yaml.safe_load(runbook_file.read_text())
    assert data["status"] == "verified"
    validate_runbook(data)  # conforms to the Runbook schema (ADR-0010)


def test_verify_failure_yields_failure_report(tmp_path, git_origin):
    origin, _first, _second = git_origin
    # executor answers nothing acceptable -> healthcheck fails
    failing = FakeSandboxExecutor(ports={3000: 49152}, responses={"/": 500})
    final = _run(failing, tmp_path, origin)

    assert final["verified"] is False
    assert final["runbook"]["status"] == "failed"
    report = (tmp_path / "report.md").read_text().lower()
    assert "not verified" in report or "failed" in report


def test_macro_phases_are_the_documented_dag():
    assert MACRO_PHASES == [
        "clone",
        "profile",
        "plan",
        "verify",
        "discover",
        "test",
        "report",
    ]
