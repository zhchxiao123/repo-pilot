"""Integration: a multi-component Run Plan verifies against a real Docker daemon (#38).

flask-db expressed as two components — a managed postgres (native-cmd oracle, waited
on via compose healthcheck) and the app backend (http oracle, probed post-up). The
sandbox adjudicates both oracles. Requires Docker; excluded from the default run:
  REPO_PILOT_COMPOSE_CMD="sudo docker compose" pytest -m integration -o addopts=""
"""

import shutil
import tempfile

import pytest

from repo_pilot.compose import compile_components
from repo_pilot.component_oracles import verify_component
from repo_pilot.executor import DockerSandboxExecutor

pytestmark = pytest.mark.integration

COMPONENTS = [
    {"name": "postgres", "image": "postgres:16",
     "env": {"POSTGRES_USER": "app", "POSTGRES_PASSWORD": "app", "POSTGRES_DB": "app"},
     "oracle": {"type": "native-cmd", "command": "pg_isready -U app"}},
    {"name": "backend", "image": "python:3.11-bookworm", "workdir": "/app",
     "command": "pip install -r requirements.txt && python app.py",
     "ports": [8000], "depends_on": ["postgres"],
     "env": {"DATABASE_URL": "postgresql://app:app@postgres:5432/app"},
     "oracle": {"type": "http", "port": 8000, "path": "/health"}},
]


def test_component_system_verifies_in_real_docker(fixture_repo):
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    # copy the fixture out so the executor's generated Dockerfiles don't touch source
    repo = tempfile.mkdtemp()
    shutil.copytree(fixture_repo("flask-db"), repo, dirs_exist_ok=True)

    sandbox = DockerSandboxExecutor().start(compile_components(COMPONENTS), repo_dir=repo)
    try:
        results = {
            c["name"]: verify_component(c, sandbox, retries=40, poll_interval=3.0)
            for c in COMPONENTS
        }
        assert results["postgres"].passed, results["postgres"].detail
        assert results["backend"].passed, results["backend"].detail
    finally:
        sandbox.stop()
        shutil.rmtree(repo, ignore_errors=True)


CLI_COMPONENTS = [
    {"name": "cli", "role": "cli", "image": "python:3.11-slim", "workdir": "/workspace/repo",
     "command": "python cli.py --upper hello", "oracle": {"type": "functional-smoke"}},
]

LIB_COMPONENTS = [
    {"name": "tests", "role": "library", "image": "python:3.11-slim", "workdir": "/workspace/repo",
     "command": "pip install -q pytest && python -m pytest -q", "oracle": {"type": "tests-pass"}},
]


def _verify_shape(fixture_name, components, fixture_repo):
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    repo = tempfile.mkdtemp()
    shutil.copytree(fixture_repo(fixture_name), repo, dirs_exist_ok=True)
    sandbox = DockerSandboxExecutor().start(compile_components(components), repo_dir=repo)
    try:
        return {c["name"]: verify_component(c, sandbox, retries=40, poll_interval=3.0)
                for c in components}
    finally:
        sandbox.stop()
        shutil.rmtree(repo, ignore_errors=True)


def test_cli_component_verifies_by_running_a_subcommand(fixture_repo):
    # non-service success: a CLI that runs its function and exits 0 (#41/#43)
    results = _verify_shape("cli-min", CLI_COMPONENTS, fixture_repo)
    assert results["cli"].passed, results["cli"].detail


def test_library_component_verifies_when_tests_pass(fixture_repo):
    results = _verify_shape("lib-min", LIB_COMPONENTS, fixture_repo)
    assert results["tests"].passed, results["tests"].detail
