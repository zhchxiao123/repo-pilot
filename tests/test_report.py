"""Report Writer renders a Markdown report containing the repo facts (§14.6)."""

from repo_pilot.cloner import RepoRef
from repo_pilot.report import render_report


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
