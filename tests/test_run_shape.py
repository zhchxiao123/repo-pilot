"""Tests for the canonical Run Shape model (Task 1).

Pure model: no Docker, LangGraph, YAML, or JSON Schema. These tests pin the
invariants that later phases (projection, detection, planning) rely on.
"""

from __future__ import annotations

import pytest

from repo_pilot.run_shape import (
    ORACLE_PRIMARY_SHAPE,
    SHAPE_ORACLES,
    Oracle,
    RunComponent,
    RunPlan,
    RunShape,
    normalize_plan,
)


def test_service_plan_requires_repo_code_component():
    plan = RunPlan(
        id="p1",
        shape=RunShape.SERVICE,
        components=[
            RunComponent(
                name="web",
                image="python:3.11",
                workdir="/workspace/repo",
                command="python app.py",
                ports=[8000],
                oracle=Oracle(type="http", port=8000, path="/health"),
            )
        ],
    )
    normalized = normalize_plan(plan)
    assert normalized.primary_component().name == "web"
    assert normalized.runnable is True


def test_docs_shape_is_not_runnable_without_components():
    plan = RunPlan(id="docs", shape=RunShape.DOCS, components=[])
    assert normalize_plan(plan).runnable is False


def test_docs_plan_rejects_oracle():
    plan = RunPlan(
        id="d",
        shape=RunShape.DOCS,
        components=[RunComponent(name="c", image="python:3.11", oracle=Oracle(type="http"))],
    )
    with pytest.raises(ValueError):
        normalize_plan(plan)


def test_cli_plan_rejects_service_oracle():
    plan = RunPlan(
        id="c",
        shape=RunShape.CLI,
        components=[
            RunComponent(name="c", image="python:3.11", oracle=Oracle(type="http", port=80))
        ],
    )
    with pytest.raises(ValueError):
        normalize_plan(plan)


def test_runnable_component_requires_image():
    plan = RunPlan(
        id="noimg",
        shape=RunShape.CLI,
        components=[RunComponent(name="c", oracle=Oracle(type="functional-smoke"))],
    )
    with pytest.raises(ValueError):
        normalize_plan(plan)


def test_shape_oracles_and_primary_shape_are_consistent():
    # Every oracle that maps to a primary shape must be valid for that shape.
    for oracle_type, shape in ORACLE_PRIMARY_SHAPE.items():
        assert oracle_type in SHAPE_ORACLES[shape]
