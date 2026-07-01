"""ArtifactStore lays out a self-contained per-job directory (§15.2)."""

from pathlib import Path

from repo_pilot.artifacts import ArtifactStore


def test_create_job_makes_a_per_job_directory(tmp_path):
    store = ArtifactStore(tmp_path)
    job = store.create_job("job-123")
    assert job.dir.is_dir()
    assert job.dir == tmp_path / "job-123"


def test_job_exposes_typed_subpaths_under_its_dir(tmp_path):
    job = ArtifactStore(tmp_path).create_job("job-123")
    for p in (job.profile_path, job.evidence_path, job.runbook_path, job.report_path):
        assert Path(p).parent == job.dir
    assert job.logs_dir.is_dir()
    assert job.logs_dir == job.dir / "logs"


def test_create_job_generates_an_id_when_none_given(tmp_path):
    store = ArtifactStore(tmp_path)
    a = store.create_job()
    b = store.create_job()
    assert a.job_id != b.job_id
    assert a.dir.is_dir() and b.dir.is_dir()
