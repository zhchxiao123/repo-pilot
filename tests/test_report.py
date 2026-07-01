"""Report Writer renders a Markdown report containing the repo facts (§14.6)."""

from repo_pilot.cloner import RepoRef
from repo_pilot.report import render_report


def test_report_contains_repo_url_commit_and_branch(tmp_path):
    ref = RepoRef(repo_dir=tmp_path, commit="abc123", default_branch="main")
    md = render_report("https://github.com/org/repo", ref)
    assert "https://github.com/org/repo" in md
    assert "abc123" in md
    assert "main" in md
