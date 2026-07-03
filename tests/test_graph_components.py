"""The graph verifies a multi-component Run Plan end-to-end (#38).

The plan phase is stubbed to emit a component Run Plan (agent decomposition is
#40); the fake executor serves canned per-component ports / compose state so the
component-verify path runs with no Docker. The sandbox adjudicates every oracle.
"""

import pytest
import yaml

from repo_pilot import graph as graph_module
from repo_pilot.candidate_planning import PlanningResult
from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.graph import build_graph, initial_state
from repo_pilot.runbook_projection import runbook_to_plan
from repo_pilot.schemas import validate_runbook

COMPONENT_RUNBOOK = {
    "schema_version": "v1",
    "id": "fullstack",
    "status": "candidate",
    "repo": {"url": "https://github.com/org/repo", "commit": "abc123"},
    "runtime": {"image": "python:3.11", "workdir": "/app"},
    "steps": {"start": [{"command": "uvicorn app:app --port 8000"}]},
    "healthcheck": {"strategy": "http"},
    "evidence_refs": ["ev_agent1"],
    "components": [
        {"name": "db", "image": "postgres:16",
         "oracle": {"type": "native-cmd", "command": "pg_isready -U app"}},
        {"name": "backend", "image": "python:3.11", "workdir": "/app",
         "command": "uvicorn app:app --port 8000", "ports": [8000], "depends_on": ["db"],
         "oracle": {"type": "http", "port": 8000, "path": "/health"}},
    ],
}


def _run(executor, tmp_path, origin):
    graph = build_graph(executor)
    return graph.invoke(
        initial_state(
            repo_url=str(origin), commit=None,
            repo_dir=str(tmp_path / "work" / "repo"),
            report_path=str(tmp_path / "report.md"),
            runbook_path=str(tmp_path / "runbook.yaml"),
            profile_path=str(tmp_path / "repo-profile.json"),
            evidence_path=str(tmp_path / "evidence.jsonl"),
        )
    )


@pytest.fixture
def _stub_plan(monkeypatch):
    import copy
    monkeypatch.setattr(
        graph_module, "plan_candidates",
        lambda profile, evidence: PlanningResult(
            candidates=[runbook_to_plan(copy.deepcopy(COMPONENT_RUNBOOK))],
            classification="service",
        ),
    )


def _healthy_executor():
    # db healthy (compose waited on it), backend serving /health, port published
    return FakeSandboxExecutor(
        component_ports={"backend": {8000: 49152}},
        states={"db": ("running", "healthy", None), "backend": ("running", None, None)},
        responses={"/health": 200},
    )


def test_component_system_verifies_when_all_oracles_pass(
    tmp_path, git_repo_from, fixture_repo, _stub_plan
):
    origin, _ = git_repo_from(fixture_repo("express-min"))
    final = _run(_healthy_executor(), tmp_path, origin)

    assert final["verified"] is True
    rb = final["runbook"]
    assert rb["status"] == "verified"
    results = {c["name"]: c for c in rb["verification"]["components"]}
    assert results["db"]["passed"] and results["db"]["oracle"] == "native-cmd"
    assert results["backend"]["passed"] and results["backend"]["oracle"] == "http"
    validate_runbook(yaml.safe_load((tmp_path / "runbook.yaml").read_text()))


def test_verified_multicomponent_persists_compose_and_reproduces_it(
    tmp_path, git_repo_from, fixture_repo, _stub_plan
):
    origin, _ = git_repo_from(fixture_repo("express-min"))
    _run(_healthy_executor(), tmp_path, origin)

    compose_file = tmp_path / "compose.generated.yaml"
    assert compose_file.is_file()  # the generated stack is persisted as an artifact
    report = (tmp_path / "report.md").read_text()
    # reproduce references the persisted compose, not a bare `docker compose up`
    assert "compose.generated.yaml" in report


def test_component_system_fails_when_one_oracle_fails(
    tmp_path, git_repo_from, fixture_repo, _stub_plan
):
    # backend never serves /health -> system is not "running", even though db is up
    executor = FakeSandboxExecutor(
        component_ports={"backend": {8000: 49152}},
        states={"db": ("running", "healthy", None), "backend": ("running", None, None)},
        responses={"/health": 502},
    )
    final = _run(executor, tmp_path, origin=git_repo_from(fixture_repo("express-min"))[0])

    assert final["verified"] is False
    results = {c["name"]: c for c in final["runbook"]["verification"]["components"]}
    assert results["db"]["passed"] is True
    assert results["backend"]["passed"] is False


CLI_RUNBOOK = {
    "schema_version": "v1", "id": "cli", "status": "candidate",
    "repo": {"url": "https://github.com/org/repo", "commit": "abc123"},
    "runtime": {"image": "python:3.11", "workdir": "/workspace/repo"},
    "steps": {"start": [{"command": "pip install -e . && mytool convert sample.txt"}]},
    "healthcheck": {"strategy": "http"}, "evidence_refs": ["ev_agent1"],
    "components": [
        {"name": "cli", "role": "cli", "image": "python:3.11", "workdir": "/workspace/repo",
         "command": "pip install -e . && mytool convert sample.txt",
         "oracle": {"type": "functional-smoke"}},
    ],
}


def test_non_service_cli_verifies_by_running_to_a_clean_exit(
    tmp_path, git_repo_from, fixture_repo, monkeypatch
):
    import copy
    monkeypatch.setattr(
        graph_module, "plan_candidates",
        lambda p, e: PlanningResult(
            candidates=[runbook_to_plan(copy.deepcopy(CLI_RUNBOOK))], classification="cli"
        ),
    )
    # the CLI ran its subcommand and exited 0 -> functional-smoke reached
    executor = FakeSandboxExecutor(states={"cli": ("exited", None, 0)})
    origin, _ = git_repo_from(fixture_repo("express-min"))
    final = _run(executor, tmp_path, origin)

    assert final["verified"] is True
    comp = final["runbook"]["verification"]["components"][0]
    assert comp["oracle"] == "functional-smoke" and comp["passed"] is True
    report = (tmp_path / "report.md").read_text()
    assert "cli: functional-smoke" in report
