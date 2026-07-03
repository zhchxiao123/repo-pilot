"""Tests for the terminal Outcome model (Task 4)."""

from __future__ import annotations

from repo_pilot.outcome import (
    Outcome,
    OutcomeKind,
    outcome_from_state,
    outcome_from_verification,
)
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, RunShape, normalize_plan
from repo_pilot.run_verifier import RunVerification


# --- canonical derivation (built first) ---------------------------------------


def _cli_plan():
    return normalize_plan(
        RunPlan(
            id="cli",
            shape=RunShape.CLI,
            components=[
                RunComponent(name="cli", image="python:3.11", command="tool x",
                             oracle=Oracle(type="functional-smoke"))
            ],
        )
    )


def test_outcome_from_verification_verified_cli():
    out = outcome_from_verification(_cli_plan(), RunVerification(verified=True, logs_summary=""))
    assert out.kind == OutcomeKind.VERIFIED
    assert out.shape == "cli"
    assert out.verified is True


def test_outcome_from_verification_failed():
    out = outcome_from_verification(_cli_plan(), RunVerification(verified=False, logs_summary=""))
    assert out.kind == OutcomeKind.FAILED
    assert out.verified is False


def test_outcome_from_verification_docs_is_not_runnable():
    docs = normalize_plan(RunPlan(id="d", shape=RunShape.DOCS, components=[]))
    assert outcome_from_verification(docs, None).kind == OutcomeKind.NOT_RUNNABLE


# --- state adapter (compat) ---------------------------------------------------


def test_verified_cli_is_verified_not_not_a_service():
    state = {"verified": True, "classification": "cli", "runbook": {"id": "cli"}}
    assert outcome_from_state(state).kind == OutcomeKind.VERIFIED


def test_docs_without_candidate_is_not_runnable():
    state = {"classification": "docs", "deferred_reason": "not-a-service:docs"}
    out = outcome_from_state(state)
    assert out.kind == OutcomeKind.NOT_RUNNABLE
    assert out.shape == "docs"


def test_failed_state_is_failed():
    out = outcome_from_state({"runbook": {"id": "x"}, "verified": False})
    assert out.kind == OutcomeKind.FAILED


def test_needs_compose_is_deferred():
    out = outcome_from_state({"deferred_reason": "needs-compose"})
    assert out.kind == OutcomeKind.DEFERRED


def test_empty_state_is_no_candidate():
    assert outcome_from_state({}).kind == OutcomeKind.NO_CANDIDATE
