"""Report Writer renders a Markdown report containing the repo facts (§14.6)."""

from repo_pilot.cloner import RepoRef
from repo_pilot.report import render_report
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, RunShape
from repo_pilot.runbook_projection import plan_to_runbook


def test_report_contains_repo_url_commit_and_branch(tmp_path):
    ref = RepoRef(repo_dir=tmp_path, commit="abc123", default_branch="main")
    md = render_report("https://github.com/org/repo", ref)
    assert "https://github.com/org/repo" in md
    assert "abc123" in md
    assert "main" in md


def _ref(tmp_path):
    return RepoRef(repo_dir=tmp_path, commit="c1", default_branch="main")


def test_report_renders_component_verdicts_when_verified(tmp_path):
    runbook = {
        "id": "fullstack", "status": "verified",
        "verification": {
            "healthcheck_result": {"passed": True},
            "components": [
                {"name": "db", "oracle": "native-cmd", "passed": True, "detail": "health=healthy"},
                {"name": "backend", "oracle": "http", "passed": True, "detail": "GET /health -> 200"},
            ],
        },
    }
    md = render_report("u", _ref(tmp_path), runbook=runbook, classification="service")
    assert "Classification: service" in md
    assert "### Components" in md
    assert "db: native-cmd — reached" in md
    assert "backend: http — reached" in md
    assert "None at None" not in md  # no misleading single-app healthcheck line


def test_report_flags_the_unreached_component_on_failure(tmp_path):
    runbook = {
        "id": "fullstack", "status": "failed",
        "verification": {
            "healthcheck_result": {"passed": False},
            "components": [
                {"name": "db", "oracle": "native-cmd", "passed": True, "detail": "healthy"},
                {"name": "backend", "oracle": "http", "passed": False, "detail": "GET /health -> 502"},
            ],
        },
    }
    md = render_report("u", _ref(tmp_path), runbook=runbook)
    assert "backend: http — NOT reached" in md
    assert "db: native-cmd — reached" in md


def _verified_runbook(plan: RunPlan, component_results: list[dict]) -> dict:
    rb = plan_to_runbook(plan, status="verified")
    rb["verification"] = {"components": component_results}
    return rb


def test_report_shows_outcome_and_reproduce_for_verified_cli(tmp_path):
    plan = RunPlan(
        id="cli", shape=RunShape.CLI, repo={"url": "u", "commit": "c"},
        components=[RunComponent(name="cli", image="python:3.11", workdir="/workspace/repo",
                    command="pip install -e . && sample", oracle=Oracle(type="functional-smoke"))],
    )
    rb = _verified_runbook(
        plan, [{"name": "cli", "oracle": "functional-smoke", "passed": True, "detail": "exited 0"}]
    )
    md = render_report("https://x/y", _ref(tmp_path), runbook=rb, classification="cli")
    assert "## Outcome" in md
    assert "Verdict: verified" in md
    assert "Shape: cli" in md
    assert "Exercised by: functional-smoke" in md
    assert "## Run Plan" in md
    assert "cli: python:3.11" in md
    assert "oracle: functional-smoke" in md
    assert "## Reproduce" in md
    assert "pip install -e . && sample" in md  # single-component reproduce = its command


def test_report_verified_library_shows_tests_pass(tmp_path):
    plan = RunPlan(
        id="lib", shape=RunShape.LIBRARY, repo={"url": "u", "commit": "c"},
        components=[RunComponent(name="lib", image="python:3.11", workdir="/workspace/repo",
                    command="pip install -e . && pytest", oracle=Oracle(type="tests-pass", command="pytest"))],
    )
    rb = _verified_runbook(plan, [{"name": "lib", "oracle": "tests-pass", "passed": True, "detail": "exited 0"}])
    md = render_report("u", _ref(tmp_path), runbook=rb, classification="library")
    assert "Shape: library" in md
    assert "Exercised by: tests-pass" in md


def test_report_multi_component_reproduce_uses_compose(tmp_path):
    plan = RunPlan(
        id="mc", shape=RunShape.MULTI_COMPONENT_SERVICE, repo={"url": "u", "commit": "c"},
        components=[
            RunComponent(name="db", image="postgres:16", role="db",
                         oracle=Oracle(type="native-cmd", command="pg_isready")),
            RunComponent(name="backend", image="python:3.11", role="backend",
                         command="uvicorn app:app", ports=[8000],
                         oracle=Oracle(type="http", port=8000, path="/health")),
        ],
    )
    rb = _verified_runbook(plan, [
        {"name": "db", "oracle": "native-cmd", "passed": True, "detail": "healthy"},
        {"name": "backend", "oracle": "http", "passed": True, "detail": "200"},
    ])
    md = render_report("u", _ref(tmp_path), runbook=rb)
    assert "## Reproduce" in md
    assert "docker compose up" in md  # dependency components have no command of their own


def test_report_docs_is_not_runnable(tmp_path):
    md = render_report("u", _ref(tmp_path), runbook=None,
                       deferred_reason="not-a-service:docs", classification="docs")
    assert "Verdict: not_runnable" in md
    assert "Shape: docs" in md
    assert "not a runnable service" in md
