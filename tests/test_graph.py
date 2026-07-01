"""The macro-skeleton graph runs every phase in order and reaches a verdict.

The Sandbox Executor is injected (ADR-0004 seam) so the whole pipeline runs with
no Docker.
"""

import json

import yaml

from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.graph import MACRO_PHASES, build_graph, initial_state
from repo_pilot.schemas import validate_evidence, validate_profile, validate_runbook


def _run(executor, tmp_path, origin):
    graph = build_graph(executor)
    return graph.invoke(
        initial_state(
            repo_url=str(origin),
            commit=None,
            repo_dir=str(tmp_path / "work" / "repo"),
            report_path=str(tmp_path / "report.md"),
            runbook_path=str(tmp_path / "runbook.yaml"),
            profile_path=str(tmp_path / "repo-profile.json"),
            evidence_path=str(tmp_path / "evidence.jsonl"),
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


def test_verify_pass_marks_runbook_verified(tmp_path, git_repo_from, fixture_repo):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    final = _run(_success_executor(), tmp_path, origin)

    assert final["verified"] is True
    assert final["runbook"]["status"] == "verified"
    assert final["runbook"]["verification"]["healthcheck_result"]["passed"] is True
    # the runbook was evidence-derived, not hardcoded
    assert final["runbook"]["evidence_refs"]
    assert "verified" in (tmp_path / "report.md").read_text().lower()


def test_verified_runbook_is_persisted_and_schema_valid(
    tmp_path, git_repo_from, fixture_repo
):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    _run(_success_executor(), tmp_path, origin)

    runbook_file = tmp_path / "runbook.yaml"
    assert runbook_file.is_file()
    data = yaml.safe_load(runbook_file.read_text())
    assert data["status"] == "verified"
    validate_runbook(data)  # conforms to the Runbook schema (ADR-0010)


def test_verify_failure_yields_failure_report_with_logs(
    tmp_path, git_repo_from, fixture_repo
):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    # executor answers nothing acceptable -> healthcheck fails; logs are captured
    failing = FakeSandboxExecutor(
        ports={3000: 49152}, responses={"/": 500}, logs="npm ERR! boom"
    )
    final = _run(failing, tmp_path, origin)

    assert final["verified"] is False
    assert final["runbook"]["status"] == "failed"
    report = (tmp_path / "report.md").read_text()
    assert "not verified" in report.lower()
    assert "boom" in report  # captured logs surfaced in the failure report


def test_profile_phase_writes_valid_artifacts(tmp_path, git_repo_from, fixture_repo):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    _run(_success_executor(), tmp_path, origin)

    profile = json.loads((tmp_path / "repo-profile.json").read_text())
    validate_profile(profile)
    assert "javascript" in profile["languages"]
    assert "express" in profile["frameworks"]

    lines = (tmp_path / "evidence.jsonl").read_text().splitlines()
    evidence = [json.loads(line) for line in lines]
    for item in evidence:
        validate_evidence(item)
    ids = {e["id"] for e in evidence}
    # entrypoint conclusions resolve to real evidence items
    for entry in profile["entrypoints"]:
        assert set(entry["evidence_refs"]) <= ids


def test_compose_only_repo_yields_deferred_report(tmp_path, git_repo_from):
    src = tmp_path / "composeonly"
    src.mkdir()
    (src / "docker-compose.yml").write_text("services: {}\n")
    origin, _commit = git_repo_from(src)

    final = _run(_success_executor(), tmp_path, origin)
    assert final.get("runbook") is None
    assert final["deferred_reason"] == "needs-compose"
    assert "deferred" in (tmp_path / "report.md").read_text().lower()


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
