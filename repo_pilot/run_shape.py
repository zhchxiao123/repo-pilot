"""Canonical Run Shape model (Task 1).

The internal source of truth for "how does this repo run?" is a ``RunPlan``:
a ``shape`` (service, cli, library, ...), one or more ``RunComponent``s, and a
shape-specific readiness ``Oracle`` per component. Planning, verification,
reporting, and evaluation consume this model; the persisted v1 Runbook becomes a
projection of it (see ``runbook_projection``).

This module is deliberately **pure**: no Docker, LangGraph, YAML, JSON Schema, or
LangChain imports. It owns the vocabulary and the invariants, nothing else.

``SHAPE_ORACLES`` is the single source of truth for which oracle types are valid
for which shape; ``ORACLE_PRIMARY_SHAPE`` is its partial inverse, used by
projection to recover a shape from an *unambiguous* oracle. Ambiguous oracles
that span shapes (``exit-zero`` for batch/build, ``http`` for service) are
deliberately absent from the inverse: shape then comes from the component
``role``, never from a shared oracle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RunShape(str, Enum):
    SERVICE = "service"
    MULTI_COMPONENT_SERVICE = "multi_component_service"
    CLI = "cli"
    LIBRARY = "library"
    BATCH = "batch"
    BUILD = "build"
    DOCS = "docs"
    UNKNOWN = "unknown"


# Authoritative shape -> valid oracle types. Detection, projection, and planning
# all consult this so they cannot disagree.
SHAPE_ORACLES: dict[RunShape, frozenset[str]] = {
    RunShape.SERVICE: frozenset({"http", "tcp-port", "log-ready", "process-up"}),
    RunShape.MULTI_COMPONENT_SERVICE: frozenset(
        {"http", "tcp-port", "native-cmd", "log-ready", "process-up"}
    ),
    RunShape.CLI: frozenset({"functional-smoke", "exit-zero", "stdio-handshake"}),
    RunShape.LIBRARY: frozenset({"tests-pass"}),
    RunShape.BATCH: frozenset({"exit-zero"}),
    RunShape.BUILD: frozenset({"build-succeeds", "exit-zero"}),
    RunShape.DOCS: frozenset(),  # not runnable
    RunShape.UNKNOWN: frozenset(),
}

# Partial inverse of SHAPE_ORACLES for projection. Ambiguous oracles that span
# shapes (exit-zero, http) are intentionally omitted: shape then comes from the
# component role, never from a shared oracle. This is what makes the
# projection round-trip (runbook_to_plan . plan_to_runbook) preserve shape.
ORACLE_PRIMARY_SHAPE: dict[str, RunShape] = {
    "tests-pass": RunShape.LIBRARY,
    "build-succeeds": RunShape.BUILD,
    "functional-smoke": RunShape.CLI,
    "stdio-handshake": RunShape.CLI,
    "native-cmd": RunShape.MULTI_COMPONENT_SERVICE,
    "log-ready": RunShape.SERVICE,
}


@dataclass
class Oracle:
    """A shape-specific readiness/success check for one component.

    Fields mirror the v1 runbook oracle schema (``type``/``port``/``path``/
    ``command``/``pattern``) plus ``acceptable_status`` for HTTP checks.
    """

    type: str
    port: int | None = None
    path: str | None = None
    command: str | None = None
    pattern: str | None = None
    acceptable_status: list[int] | None = None


@dataclass
class RunComponent:
    """One runnable unit: an image, an optional foreground command, and an oracle.

    A component with a ``command`` is repo-code (untrusted); a command-less
    component is a dependency image (e.g. postgres) whose oracle is typically a
    ``native-cmd`` compose healthcheck.
    """

    name: str
    image: str | None = None
    role: str | None = None
    workdir: str | None = None
    command: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    ports: list[int] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    oracle: Oracle | None = None


@dataclass
class RunPlan:
    """A candidate way to run a repo: a shape plus its components.

    ``repo`` mirrors the v1 ``{"url", "commit"}`` block; ``evidence_refs`` keeps
    provenance attached through planning and repair (Refactor Principle 4).
    """

    id: str
    shape: RunShape
    components: list[RunComponent] = field(default_factory=list)
    confidence: float | None = None
    evidence_refs: list[str] = field(default_factory=list)
    repo: dict | None = None
    rationale: str | None = None
    source: str | None = None


# Shapes that describe a repo which is honestly not runnable.
_NOT_RUNNABLE_SHAPES = frozenset({RunShape.DOCS, RunShape.UNKNOWN})


@dataclass
class NormalizedRunPlan:
    """A validated ``RunPlan`` with convenience accessors.

    Produced by ``normalize_plan``; carrying this type (rather than a raw plan)
    signals that invariants have already been checked.
    """

    plan: RunPlan

    @property
    def shape(self) -> RunShape:
        return self.plan.shape

    @property
    def runnable(self) -> bool:
        return self.plan.shape not in _NOT_RUNNABLE_SHAPES and bool(self.plan.components)

    def primary_component(self) -> RunComponent:
        """The main repo-code component (has a command), else the first component."""
        for comp in self.plan.components:
            if comp.command:
                return comp
        return self.plan.components[0]

    def repo_code_components(self) -> list[RunComponent]:
        """Components that run repo code (carry a foreground command)."""
        return [c for c in self.plan.components if c.command]

    def reproduce_commands(self) -> list[str]:
        """The in-repo commands to reproduce this run, at shape-appropriate
        granularity. A multi-component system reproduces via compose (dependency
        components have no command of their own); a single-component shape (cli,
        library, build, batch, single service) reproduces via its command."""
        if len(self.plan.components) > 1:
            return ["docker compose up"]
        if self.plan.components:
            command = self.primary_component().command
            if command:
                return [command]
        return []


def normalize_plan(plan: RunPlan) -> NormalizedRunPlan:
    """Validate a plan's invariants and wrap it as a ``NormalizedRunPlan``.

    Invariants enforced:
    - a not-runnable shape (docs/unknown) must not carry an oracle;
    - every runnable component must declare an ``image`` (the verifier cannot
      start a component without one);
    - every oracle type must be valid for the plan's shape per ``SHAPE_ORACLES``.

    Raises ``ValueError`` on any violation.
    """
    if plan.shape in _NOT_RUNNABLE_SHAPES:
        for comp in plan.components:
            if comp.oracle is not None:
                raise ValueError(
                    f"{plan.shape.value} plan is not runnable but component "
                    f"{comp.name!r} carries an oracle"
                )
        return NormalizedRunPlan(plan)

    valid = SHAPE_ORACLES[plan.shape]
    for comp in plan.components:
        if not comp.image:
            raise ValueError(
                f"runnable component {comp.name!r} must declare an image"
            )
        if comp.oracle is None:
            raise ValueError(f"runnable component {comp.name!r} must declare an oracle")
        if comp.oracle.type not in valid:
            raise ValueError(
                f"oracle {comp.oracle.type!r} is not valid for shape "
                f"{plan.shape.value!r} (allowed: {sorted(valid)})"
            )
    return NormalizedRunPlan(plan)
