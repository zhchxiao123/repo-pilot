"""Terminal Outcome model (Task 4).

A repo's terminal result is a shape-specific *outcome*, not just an HTTP
healthcheck: a ``cli`` that ran to completion is ``verified``, a ``docs`` repo is
honestly ``not_runnable``. This module owns that taxonomy so planners, the graph,
eval, and reports stop each re-deriving verdict semantics from ad hoc state.

``outcome_from_verification`` is the canonical derivation (plan + verification ->
outcome) and is what the graph will call once it passes canonical objects.
``outcome_from_state`` is a thin compatibility adapter over the legacy graph
state, kept only until the graph is fully canonical (Task 11).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from repo_pilot.run_shape import NormalizedRunPlan
from repo_pilot.run_verifier import RunVerification


class OutcomeKind(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    DEFERRED = "deferred"
    NOT_RUNNABLE = "not_runnable"
    NO_CANDIDATE = "no_candidate"
    ERROR = "error"


# Kinds whose verdict string carries the shape (verified:cli, not_runnable:docs).
_SHAPE_QUALIFIED = frozenset({OutcomeKind.VERIFIED, OutcomeKind.NOT_RUNNABLE})


@dataclass(frozen=True)
class Outcome:
    """A terminal verdict plus the shape it applies to and a human summary."""

    kind: OutcomeKind
    shape: str = "unknown"
    summary: str = ""
    detail: str = ""
    verified: bool = False
    runnable: bool = False

    def verdict(self) -> str:
        """The eval verdict token: ``kind`` alone, or ``kind:shape`` when the
        kind is shape-qualified. This is the canonical compound vocabulary."""
        if self.kind in _SHAPE_QUALIFIED:
            return f"{self.kind.value}:{self.shape}"
        return self.kind.value


def outcome_from_verification(
    plan: NormalizedRunPlan, verification: RunVerification | None
) -> Outcome:
    """Canonical derivation: turn a (plan, verification) pair into an Outcome."""
    shape = plan.shape.value
    if verification is None:
        if not plan.runnable:
            return Outcome(
                OutcomeKind.NOT_RUNNABLE,
                shape=shape,
                summary=f"{shape}: not a runnable system",
                verified=False,
                runnable=False,
            )
        return Outcome(
            OutcomeKind.NO_CANDIDATE,
            shape=shape,
            summary=f"{shape}: no runnable candidate found",
            verified=False,
            runnable=True,
        )
    if verification.verified:
        return Outcome(
            OutcomeKind.VERIFIED,
            shape=shape,
            summary=f"{shape}: verified",
            verified=True,
            runnable=True,
        )
    failed = [c.name for c in verification.component_results if not c.passed]
    return Outcome(
        OutcomeKind.FAILED,
        shape=shape,
        summary=f"{shape}: failed",
        detail=("oracle(s) not reached: " + ", ".join(failed)) if failed else "",
        verified=False,
        runnable=True,
    )


def _shape_of_state(state: dict) -> str:
    """Best-effort shape for a legacy graph state: the agent classification, else
    the shape inferred from the runbook, else a plain service default."""
    classification = state.get("classification")
    if classification:
        return str(classification)
    runbook = state.get("runbook")
    if runbook:
        # Local import avoids a module import cycle at load time.
        from repo_pilot.runbook_projection import runbook_to_plan

        return runbook_to_plan(runbook).shape.value
    return "service"


def outcome_from_state(state: dict) -> Outcome:
    """Compatibility adapter: derive an Outcome from a legacy graph final state."""
    if state.get("verified"):
        shape = _shape_of_state(state)
        return Outcome(
            OutcomeKind.VERIFIED, shape=shape, summary=f"{shape}: verified",
            verified=True, runnable=True,
        )
    reason = state.get("deferred_reason")
    if isinstance(reason, str) and reason.startswith("not-a-service"):
        shape = reason.split(":", 1)[1] if ":" in reason else _shape_of_state(state)
        return Outcome(
            OutcomeKind.NOT_RUNNABLE, shape=shape,
            summary=f"{shape}: not a runnable system", runnable=False,
        )
    if state.get("runbook") is not None:
        shape = _shape_of_state(state)
        return Outcome(OutcomeKind.FAILED, shape=shape, summary=f"{shape}: failed", runnable=True)
    if reason:
        return Outcome(
            OutcomeKind.DEFERRED, shape=_shape_of_state(state),
            summary=f"deferred: {reason}", detail=str(reason),
        )
    return Outcome(OutcomeKind.NO_CANDIDATE, shape=_shape_of_state(state),
                   summary="no runnable candidate found")
