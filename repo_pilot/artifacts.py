"""Per-job artifact store.

Each analysis job gets a self-contained directory holding its profile, evidence,
runbook, logs, and report (§15.2). Large artifacts live here; the graph state
holds references, not blobs (ADR-0006).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Job:
    """Handle to a single job's artifact directory and its known sub-paths."""

    job_id: str
    dir: Path

    @property
    def profile_path(self) -> Path:
        return self.dir / "repo-profile.json"

    @property
    def evidence_path(self) -> Path:
        return self.dir / "evidence.jsonl"

    @property
    def runbook_path(self) -> Path:
        return self.dir / "runbook.yaml"

    @property
    def report_path(self) -> Path:
        return self.dir / "report.md"

    @property
    def logs_dir(self) -> Path:
        return self.dir / "logs"


class ArtifactStore:
    """Creates and locates per-job artifact directories under a root."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def create_job(self, job_id: str | None = None) -> Job:
        job_id = job_id or f"job-{uuid.uuid4().hex[:12]}"
        job_dir = self.root / job_id
        job = Job(job_id=job_id, dir=job_dir)
        job.logs_dir.mkdir(parents=True, exist_ok=True)
        return job
