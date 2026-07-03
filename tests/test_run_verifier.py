"""Tests for the single run-plan verifier interface (Task 3)."""

from __future__ import annotations

from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, RunShape
from repo_pilot.run_verifier import verify_run_plan


def test_verify_service_plan_with_http_oracle(tmp_path):
    plan = RunPlan(
        id="svc",
        shape=RunShape.SERVICE,
        repo={"url": "u", "commit": "c"},
        components=[
            RunComponent(
                name="app",
                image="python:3.11",
                workdir="/workspace/repo",
                command="python app.py",
                ports=[8000],
                oracle=Oracle(type="http", port=8000, path="/health"),
            )
        ],
    )
    executor = FakeSandboxExecutor(
        component_ports={"app": {8000: 49152}},
        states={"app": ("running", None, None)},
        responses={"/health": 200},
    )
    result = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert result.verified is True
    assert result.component_results[0].name == "app"


def test_verify_cli_plan_by_exit_zero(tmp_path):
    plan = RunPlan(
        id="cli",
        shape=RunShape.CLI,
        repo={"url": "u", "commit": "c"},
        components=[
            RunComponent(
                name="cli",
                image="python:3.11",
                workdir="/workspace/repo",
                command="pip install -e . && tool sample",
                oracle=Oracle(type="functional-smoke"),
            )
        ],
    )
    executor = FakeSandboxExecutor(states={"cli": ("exited", None, 0)})
    assert verify_run_plan(plan, executor, repo_dir=str(tmp_path)).verified is True


def test_verify_multi_component_fails_when_one_oracle_fails(tmp_path):
    plan = RunPlan(
        id="mc",
        shape=RunShape.MULTI_COMPONENT_SERVICE,
        repo={"url": "u", "commit": "c"},
        components=[
            RunComponent(name="db", image="postgres:16", role="db",
                         oracle=Oracle(type="native-cmd", command="pg_isready")),
            RunComponent(name="backend", image="python:3.11", role="backend",
                         command="uvicorn app:app", ports=[8000],
                         oracle=Oracle(type="http", port=8000, path="/health")),
        ],
    )
    # db healthy, but backend never publishes a port -> http oracle fails
    executor = FakeSandboxExecutor(
        states={"db": ("running", "healthy", None), "backend": ("running", None, None)},
        responses={},
    )
    result = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert result.verified is False
    by_name = {r.name: r for r in result.component_results}
    assert by_name["db"].passed is True
    assert by_name["backend"].passed is False
    assert result.sandbox is None  # stopped on failure


def test_verify_keeps_sandbox_alive_on_success(tmp_path):
    plan = RunPlan(
        id="svc", shape=RunShape.SERVICE, repo={"url": "u", "commit": "c"},
        components=[RunComponent(name="app", image="python:3.11", command="python app.py",
                    ports=[8000], oracle=Oracle(type="http", port=8000, path="/health"))],
    )
    executor = FakeSandboxExecutor(
        component_ports={"app": {8000: 49152}},
        states={"app": ("running", None, None)},
        responses={"/health": 200},
    )
    result = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert result.sandbox is not None  # kept up for discover/test
    assert result.ports == [{"container": 8000, "host": 49152}]
