"""Tests for the RunPlan <-> v1 Runbook projection (Task 2)."""

from __future__ import annotations

import pytest

from repo_pilot.run_shape import (
    Oracle,
    RunComponent,
    RunPlan,
    RunShape,
    normalize_plan,
)
from repo_pilot.runbook_projection import plan_to_runbook, runbook_to_plan
from repo_pilot.schemas import validate_runbook

REPO = {"url": "https://x/y", "commit": "abc"}


def test_component_plan_projects_to_v1_runbook():
    plan = RunPlan(
        id="fullstack",
        shape=RunShape.MULTI_COMPONENT_SERVICE,
        repo=REPO,
        evidence_refs=["ev_agent1"],
        components=[
            RunComponent(
                name="db",
                image="postgres:16",
                role="db",
                oracle=Oracle(type="native-cmd", command="pg_isready"),
            ),
            RunComponent(
                name="backend",
                image="python:3.11",
                role="backend",
                workdir="/workspace/repo",
                command="uvicorn app:app --port 8000",
                ports=[8000],
                depends_on=["db"],
                oracle=Oracle(type="http", port=8000, path="/health"),
            ),
        ],
    )
    rb = plan_to_runbook(plan, status="candidate")
    validate_runbook(rb)
    assert rb["components"][1]["name"] == "backend"
    assert rb["runtime"]["image"] == "python:3.11"


def test_legacy_single_service_runbook_imports_as_service_plan():
    rb = {
        "schema_version": "v1",
        "id": "node_npm_start",
        "status": "candidate",
        "repo": REPO,
        "runtime": {"image": "node:20-bookworm", "workdir": "/workspace/repo"},
        "steps": {"start": [{"command": "npm start", "expected_ports": [3000]}]},
        "healthcheck": {"strategy": "http", "url_candidates": ["/"]},
        "evidence_refs": ["ev_1"],
    }
    plan = runbook_to_plan(rb)
    assert plan.shape == RunShape.SERVICE
    assert plan.components[0].command == "npm start"


@pytest.mark.parametrize(
    "shape,oracle",
    [
        (RunShape.SERVICE, Oracle(type="http", port=8000, path="/health")),
        (RunShape.CLI, Oracle(type="functional-smoke")),
        (RunShape.LIBRARY, Oracle(type="tests-pass", command="pytest")),
        (RunShape.BUILD, Oracle(type="build-succeeds", command="make")),
        # BATCH uses the ambiguous exit-zero oracle, so shape can only be
        # recovered from persisted component `role` — this case guards that path.
        (RunShape.BATCH, Oracle(type="exit-zero")),
    ],
)
def test_shape_survives_projection_round_trip(shape, oracle):
    # The invariant most likely to silently break: project to v1 and back must
    # preserve shape. role is intentionally NOT set here — plan_to_runbook must
    # derive and persist it, otherwise batch silently degrades to service.
    plan = RunPlan(
        id="x",
        shape=shape,
        repo=REPO,
        components=[
            RunComponent(
                name="c",
                image="python:3.11",
                workdir="/workspace/repo",
                command="run",
                oracle=oracle,
            )
        ],
    )
    rb = plan_to_runbook(plan, status="candidate")
    validate_runbook(rb)  # projection must always be schema-valid
    assert rb["components"][0]["role"]  # projection persisted a role
    assert runbook_to_plan(rb).shape == shape


def test_multi_component_plan_round_trips_to_multi_component():
    plan = RunPlan(
        id="mc",
        shape=RunShape.MULTI_COMPONENT_SERVICE,
        repo=REPO,
        components=[
            RunComponent(name="db", image="postgres:16", role="db",
                         oracle=Oracle(type="native-cmd", command="pg_isready")),
            RunComponent(name="backend", image="python:3.11", role="backend",
                         command="uvicorn app:app", ports=[8000],
                         oracle=Oracle(type="http", port=8000, path="/health")),
        ],
    )
    rb = plan_to_runbook(plan, status="candidate")
    assert runbook_to_plan(rb).shape == RunShape.MULTI_COMPONENT_SERVICE


def test_projected_service_plan_is_normalizable():
    plan = RunPlan(
        id="svc", shape=RunShape.SERVICE, repo=REPO,
        components=[RunComponent(name="web", image="python:3.11", command="python app.py",
                    ports=[8000], oracle=Oracle(type="http", port=8000, path="/health"))],
    )
    rb = plan_to_runbook(plan, status="candidate")
    normalize_plan(runbook_to_plan(rb))  # must not raise
