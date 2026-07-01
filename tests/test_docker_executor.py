"""Real DockerSandboxExecutor integration tests (require Docker; marked slow).

These are excluded from the default run (`-m 'not integration'`) and exercise the
actual Docker path end-to-end against the express-min fixture.
"""

import shutil

import pytest

from repo_pilot.compose import compile_compose
from repo_pilot.executor import DockerSandboxExecutor
from repo_pilot.healthcheck import run_healthcheck

pytestmark = pytest.mark.integration

EXPRESS_RUNBOOK = {
    "schema_version": "v1",
    "id": "node_npm_start",
    "status": "candidate",
    "repo": {"url": "x", "commit": "x"},
    "runtime": {"image": "node:20-bookworm", "workdir": "/workspace/repo"},
    "steps": {
        "setup": [{"command": "npm install"}],
        "start": [{"command": "npm start", "expected_ports": [3000]}],
    },
    "healthcheck": {
        "strategy": "http",
        "url_candidates": ["/health", "/"],
        "acceptable_status": [200, 204, 301, 302, 404],
    },
}

UNSTARTABLE_RUNBOOK = {
    **EXPRESS_RUNBOOK,
    "steps": {"start": [{"command": "node /nonexistent.js", "expected_ports": [3000]}]},
}


@pytest.fixture(autouse=True)
def _require_docker():
    if shutil.which("docker") is None:
        pytest.skip("docker not available")


def test_express_fixture_starts_and_healthchecks(fixture_repo, tmp_path):
    # mount a copy so `npm install` does not write node_modules into the fixture
    repo = tmp_path / "repo"
    shutil.copytree(fixture_repo("express-min"), repo)
    executor = DockerSandboxExecutor()
    sandbox = executor.start(compile_compose(EXPRESS_RUNBOOK), repo_dir=str(repo))
    try:
        assert 3000 in sandbox.ports  # a real host port was published
        result = run_healthcheck(
            sandbox, EXPRESS_RUNBOOK["healthcheck"], retries=30, poll_interval=2.0
        )
        assert result.passed
        assert result.status_code in (200, 204, 301, 302, 404)
    finally:
        sandbox.stop()


def test_unstartable_app_fails_healthcheck_and_cleans_up(fixture_repo, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(fixture_repo("express-min"), repo)
    executor = DockerSandboxExecutor()
    sandbox = executor.start(compile_compose(UNSTARTABLE_RUNBOOK), repo_dir=str(repo))
    try:
        result = run_healthcheck(
            sandbox, UNSTARTABLE_RUNBOOK["healthcheck"], retries=2, poll_interval=1.0
        )
        assert not result.passed
        assert sandbox.logs  # captured logs for the failure report
    finally:
        sandbox.stop()
