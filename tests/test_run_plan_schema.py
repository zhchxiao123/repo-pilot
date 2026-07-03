"""Tests for the canonical v2 run-plan schema (Task 7)."""

from __future__ import annotations

import pytest

from repo_pilot.schemas import SchemaValidationError, validate_run_plan, validate_runbook


def test_run_plan_schema_accepts_cli_shape():
    validate_run_plan(
        {
            "schema_version": "v2",
            "id": "cli",
            "shape": "cli",
            "status": "candidate",
            "repo": {"url": "u", "commit": "c"},
            "components": [
                {
                    "name": "cli",
                    "role": "cli",
                    "image": "python:3.11",
                    "workdir": "/workspace/repo",
                    "command": "tool sample",
                    "oracle": {"type": "functional-smoke"},
                }
            ],
        }
    )


def test_run_plan_schema_does_not_require_runtime_steps_healthcheck():
    # The whole point of v2: a plan is shape + components + oracle, with no
    # legacy runtime/steps/healthcheck block.
    validate_run_plan(
        {
            "schema_version": "v2",
            "id": "svc",
            "shape": "service",
            "status": "verified",
            "repo": {"url": "u", "commit": "c"},
            "components": [
                {"name": "app", "image": "python:3.11", "command": "python app.py",
                 "ports": [8000], "oracle": {"type": "http", "port": 8000, "path": "/health"}}
            ],
            "outcome": {"kind": "verified", "shape": "service", "verified": True},
        }
    )


def test_run_plan_schema_rejects_unknown_shape():
    with pytest.raises(SchemaValidationError):
        validate_run_plan(
            {
                "schema_version": "v2",
                "id": "x",
                "shape": "banana",
                "status": "candidate",
                "repo": {"url": "u", "commit": "c"},
                "components": [
                    {"name": "c", "image": "python:3.11", "oracle": {"type": "exit-zero"}}
                ],
            }
        )


def test_v1_runbook_validation_still_works():
    # v2 must not disturb v1 artifact validation.
    validate_runbook(
        {
            "schema_version": "v1",
            "id": "node_npm_start",
            "status": "candidate",
            "repo": {"url": "u", "commit": "c"},
            "runtime": {"image": "node:20-bookworm", "workdir": "/workspace/repo"},
            "steps": {"start": [{"command": "npm start", "expected_ports": [3000]}]},
            "healthcheck": {"strategy": "http"},
        }
    )
