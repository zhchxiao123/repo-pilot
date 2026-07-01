"""The macro-skeleton graph runs every phase in order and reaches a verdict.

The Sandbox Executor is injected (ADR-0004 seam) so the whole pipeline runs with
no Docker.
"""

import json

import pytest
import yaml

from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.model_client import ReplayModelClient
from repo_pilot.graph import MACRO_PHASES, build_graph, initial_state
from repo_pilot.schemas import validate_evidence, validate_profile, validate_runbook


def _run(executor, tmp_path, origin, **build_kwargs):
    graph = build_graph(executor, **build_kwargs)
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


def test_discover_populates_targets_from_running_app(
    tmp_path, git_repo_from, fixture_repo
):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    final = _run(_success_executor(), tmp_path, origin)
    assert final["targets"]  # discovered from the live (fake) app
    assert "Test targets" in (tmp_path / "report.md").read_text()


def test_test_phase_runs_smoke_and_reports(tmp_path, git_repo_from, fixture_repo):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    ex = FakeSandboxExecutor(
        ports={3000: 49152},
        responses={"/health": 200, "/api/health": 200, "/": 200},
    )
    final = _run(ex, tmp_path, origin)
    assert final["tests"]
    assert all(t["status"] == "passed" for t in final["tests"])
    assert "Smoke tests" in (tmp_path / "report.md").read_text()


def test_broken_endpoint_is_reported_as_smoke_failure(
    tmp_path, git_repo_from, fixture_repo
):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    # /health is healthy (verify passes) but / returns 500 (smoke catches it)
    ex = FakeSandboxExecutor(
        ports={3000: 49152},
        responses={"/health": 200, "/api/health": 200, "/": 500},
    )
    _run(ex, tmp_path, origin)
    report = (tmp_path / "report.md").read_text()
    assert "FAIL" in report
    assert "curl" in report


def test_compose_only_repo_yields_deferred_report(tmp_path, git_repo_from):
    src = tmp_path / "composeonly"
    src.mkdir()
    (src / "docker-compose.yml").write_text("services: {}\n")
    origin, _commit = git_repo_from(src)

    final = _run(_success_executor(), tmp_path, origin)
    assert final.get("runbook") is None
    assert final["deferred_reason"] == "needs-compose"
    assert "deferred" in (tmp_path / "report.md").read_text().lower()


def test_runbook_carries_injected_security(tmp_path, git_repo_from, fixture_repo):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    relaxed = {"egress": "allow_private", "allow_metadata": True, "isolation": False}
    final = _run(_success_executor(), tmp_path, origin, security=relaxed)
    assert final["runbook"]["security"] == relaxed


def test_runbook_env_generated_from_env_example(tmp_path, git_repo_from, fixture_repo):
    import shutil

    src = tmp_path / "withenv"
    shutil.copytree(fixture_repo("express-min"), src)
    (src / ".env.example").write_text("DATABASE_URL=\nLOG_LEVEL=info\n")
    origin, _commit = git_repo_from(src)

    final = _run(_success_executor(), tmp_path, origin)
    generated = final["runbook"]["env"]["generated"]
    assert set(generated) == {"DATABASE_URL", "LOG_LEVEL"}
    assert all(v == "dummy" for v in generated.values())  # never real secrets


def test_logs_are_redacted_in_the_report(tmp_path, git_repo_from, fixture_repo):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    failing = FakeSandboxExecutor(
        ports={3000: 49152},
        responses={"/health": 500, "/api/health": 500, "/": 500},
        logs="db password=hunter2 during boot",
    )
    _run(failing, tmp_path, origin)
    report = (tmp_path / "report.md").read_text()
    assert "hunter2" not in report
    assert "REDACTED" in report


def test_nl_fallback_produces_candidate_when_no_deterministic(
    tmp_path, git_repo_from, fixture_repo
):
    origin, _commit = git_repo_from(fixture_repo("readme-only"))
    client = ReplayModelClient(
        [json.dumps(["pip install -r requirements.txt", "python app.py"])]
    )
    ex = FakeSandboxExecutor(
        ports={8000: 49000}, responses={"/": 200, "/health": 200, "/api/health": 200}
    )
    final = _run(ex, tmp_path, origin, model_client=client)

    rb = final["runbook"]
    assert rb["id"] == "nl_readme"
    assert rb["steps"]["start"][0]["command"] == "python app.py"
    assert rb["confidence"] == pytest.approx(0.30)
    assert client.calls  # the model was consulted
    assert final["verified"] is True  # subordination: sandbox still adjudicates


def test_nl_fallback_not_used_when_deterministic_candidate_exists(
    tmp_path, git_repo_from, fixture_repo
):
    origin, _commit = git_repo_from(fixture_repo("express-min"))
    client = ReplayModelClient([json.dumps(["should not run"])])
    final = _run(_success_executor(), tmp_path, origin, model_client=client)

    assert final["runbook"]["id"].startswith("node_")
    assert client.calls == []  # the NL seam never fired


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
