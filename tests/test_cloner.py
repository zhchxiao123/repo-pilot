"""Repo Cloner: shallow clone + optional commit checkout, recording repo facts."""

from repo_pilot.cloner import RepoCloner


def test_clone_default_branch_records_head_and_branch(tmp_path, git_origin):
    origin, _first, second = git_origin
    ref = RepoCloner().clone(str(origin), dest=tmp_path / "work")
    assert ref.commit == second
    assert ref.default_branch == "main"
    assert (ref.repo_dir / "VERSION").read_text() == "two\n"


def test_clone_checks_out_the_requested_commit(tmp_path, git_origin):
    origin, first, _second = git_origin
    ref = RepoCloner().clone(str(origin), commit=first, dest=tmp_path / "work")
    assert ref.commit == first
    assert (ref.repo_dir / "VERSION").read_text() == "one\n"
