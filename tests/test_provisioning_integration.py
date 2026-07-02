"""Integration: the agent provisions a postgres service and the app verifies (ADR-0017).

Requires Docker. Excluded from the default run; drive with
  REPO_PILOT_COMPOSE_CMD="sudo docker compose" pytest -m integration -o addopts=""
"""

import shutil
import tempfile

import pytest
from langchain_core.messages import AIMessage

from repo_pilot.executor import DockerSandboxExecutor
from repo_pilot.graph import build_graph, initial_state

pytestmark = pytest.mark.integration


class _Agent:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="", tool_calls=[{
            "name": "submit_plan", "id": "s1", "type": "tool_call",
            "args": {
                "classification": "service",
                "candidates": [{
                    "image": "python:3.11-bookworm",
                    "setup": ["pip install -r requirements.txt"],
                    "start": "python app.py",
                    "port": 8000,
                    "services": [{
                        "name": "postgres", "image": "postgres:16",
                        "env": {"POSTGRES_USER": "app", "POSTGRES_PASSWORD": "app", "POSTGRES_DB": "app"},
                        "healthcheck": "pg_isready -U app",
                    }],
                    "env": {"DATABASE_URL": "postgresql://app:app@postgres:5432/app"},
                }],
            },
        }])


def test_agent_provisioned_postgres_lets_the_app_verify(fixture_repo, git_repo_from, tmp_path):
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    origin, _ = git_repo_from(fixture_repo("flask-db"))
    graph = build_graph(
        DockerSandboxExecutor(), chat_model=_Agent(),
        healthcheck_retries=40, poll_interval=3.0,
    )
    d = tempfile.mkdtemp()
    final = graph.invoke(initial_state(
        repo_url=str(origin), commit=None, repo_dir=d + "/repo",
        report_path=d + "/r.md", runbook_path=d + "/rb.yaml",
        profile_path=d + "/p.json", evidence_path=d + "/e.jsonl",
    ))
    assert final["verified"] is True
    assert [s["name"] for s in final["runbook"]["services"]] == ["postgres"]
    assert all(t["status"] == "passed" for t in final.get("tests", []))
