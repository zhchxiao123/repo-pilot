"""Read-only, repo-confined exploration tools for the plan agent (ADR-0016)."""

import pytest

from repo_pilot.explore_tools import RepoTools


def _make_repo(tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    (tmp_path / "requirements.txt").write_text("flask\n")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "server.js").write_text("require('express')()\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret\n")
    return RepoTools(tmp_path)


def test_list_dir_skips_vcs_and_marks_dirs(tmp_path):
    tools = _make_repo(tmp_path)
    entries = tools.list_dir(".")
    assert "app.py" in entries
    assert "src/" in entries
    assert not any(".git" in e for e in entries)


def test_read_file_returns_contents(tmp_path):
    tools = _make_repo(tmp_path)
    assert "Flask" in tools.read_file("app.py")
    assert "express" in tools.read_file("src/server.js")


def test_search_finds_matches_across_repo(tmp_path):
    tools = _make_repo(tmp_path)
    hits = tools.search("Flask")
    assert any("app.py" in h for h in hits)


def test_find_by_glob(tmp_path):
    tools = _make_repo(tmp_path)
    assert "requirements.txt" in tools.find("*.txt")
    assert "src/server.js" in tools.find("**/*.js")


def test_path_traversal_is_blocked(tmp_path):
    tools = _make_repo(tmp_path)
    (tmp_path.parent / "secret.txt").write_text("HOST SECRET\n")
    with pytest.raises(ValueError):
        tools.read_file("../secret.txt")
    with pytest.raises(ValueError):
        tools.read_file("/etc/passwd")


def test_symlink_to_host_file_is_not_read(tmp_path):
    # an untrusted repo drops a symlink pointing outside the repo
    secret = tmp_path.parent / "host_secret.txt"
    secret.write_text("AWS_SECRET=hunter2\n")
    tools = _make_repo(tmp_path)
    (tmp_path / "creds.txt").symlink_to(secret)

    assert not any("hunter2" in h for h in tools.search("hunter2"))  # not leaked via search
    assert "creds.txt" not in tools.find("*.txt")                     # not exposed via find


def test_read_file_size_capped(tmp_path):
    tools = _make_repo(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 200_000)
    out = tools.read_file("big.txt")
    assert len(out) <= 64_000 + 100  # capped (plus a truncation note)


def test_missing_file_is_a_friendly_message_not_a_crash(tmp_path):
    tools = _make_repo(tmp_path)
    assert "not found" in tools.read_file("nope.py").lower()
