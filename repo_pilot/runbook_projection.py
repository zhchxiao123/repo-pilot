"""Projection between the canonical ``RunPlan`` and the persisted v1 Runbook.

The v1 Runbook (``schemas/runbook.schema.json``) stays the persisted, schema-
validated artifact during the transition; the canonical ``RunPlan`` is the
internal source of truth. This module is the *only* place that knows how the two
map, keeping schema/persistence concerns out of planners and verifiers.

Two invariants make projection safe:

- ``plan_to_runbook`` always emits ``components[]`` **and** persists each
  component's ``role`` (derived from the plan shape when unset). v1 has no
  top-level ``shape`` field (its schema forbids extra keys), so ``role`` is the
  only channel that lets ``runbook_to_plan`` recover a shape whose oracle is
  ambiguous (``exit-zero`` for batch, ``http`` for service).
- ``plan_to_runbook`` also synthesizes the legacy ``runtime``/``steps``/
  ``healthcheck`` block from the primary repo-code component so old readers and
  the current schema keep working.
"""

from __future__ import annotations

from repo_pilot.run_shape import (
    ORACLE_PRIMARY_SHAPE,
    Oracle,
    RunComponent,
    RunPlan,
    RunShape,
    normalize_plan,
)

# Shape <-> role for single-purpose shapes. Multi-component services keep their
# per-component roles (backend/db/...) and are recovered structurally (>1
# component), so they are intentionally absent here.
_SHAPE_ROLE = {
    RunShape.SERVICE: "service",
    RunShape.CLI: "cli",
    RunShape.LIBRARY: "library",
    RunShape.BATCH: "batch",
    RunShape.BUILD: "build",
}
_ROLE_SHAPE = {
    "cli": RunShape.CLI,
    "library": RunShape.LIBRARY,
    "batch": RunShape.BATCH,
    "build": RunShape.BUILD,
    "service": RunShape.SERVICE,
}

_DEFAULT_IMAGE = "python:3.11"
_DEFAULT_WORKDIR = "/workspace/repo"


def _oracle_to_dict(oracle: Oracle) -> dict:
    out: dict = {"type": oracle.type}
    for f in ("port", "path", "command", "pattern", "acceptable_status"):
        v = getattr(oracle, f)
        if v is not None:
            out[f] = v
    return out


def _component_to_dict(comp: RunComponent, role: str | None) -> dict:
    out: dict = {"name": comp.name, "image": comp.image or _DEFAULT_IMAGE}
    role = comp.role or role
    if role:
        out["role"] = role
    if comp.workdir:
        out["workdir"] = comp.workdir
    if comp.command:
        out["command"] = comp.command
    if comp.env:
        out["env"] = dict(comp.env)
    if comp.ports:
        out["ports"] = list(comp.ports)
    if comp.depends_on:
        out["depends_on"] = list(comp.depends_on)
    if comp.oracle is not None:
        out["oracle"] = _oracle_to_dict(comp.oracle)
    return out


def _legacy_healthcheck(primary: RunComponent) -> dict:
    """Synthesize a v1 healthcheck block for schema compatibility.

    An ``http`` service oracle maps naturally; every other shape uses a minimal
    ``{"strategy": "http"}`` stub only to satisfy the current required field
    (schema v2 removes this — Task 7). Verification never reads this block for
    non-service shapes; the component oracle is authoritative.
    """
    oracle = primary.oracle
    if oracle is not None and oracle.type == "http":
        hc: dict = {"strategy": "http"}
        if oracle.path:
            hc["url_candidates"] = [oracle.path]
        if oracle.acceptable_status:
            hc["acceptable_status"] = list(oracle.acceptable_status)
        return hc
    return {"strategy": "http"}


def plan_to_runbook(plan: RunPlan, status: str = "candidate") -> dict:
    """Project a canonical ``RunPlan`` to a schema-valid v1 Runbook dict."""
    normalized = normalize_plan(plan)
    primary = normalized.primary_component()
    role_default = _SHAPE_ROLE.get(plan.shape)

    runbook: dict = {
        "schema_version": "v1",
        "id": plan.id,
        "status": status,
        "repo": plan.repo or {},
        "runtime": {
            "image": primary.image or _DEFAULT_IMAGE,
            "workdir": primary.workdir or _DEFAULT_WORKDIR,
        },
        "steps": {
            "start": [
                {
                    "command": primary.command or "",
                    "expected_ports": list(primary.ports),
                }
            ]
        },
        "healthcheck": _legacy_healthcheck(primary),
        "components": [_component_to_dict(c, role_default) for c in plan.components],
    }
    if plan.confidence is not None:
        runbook["confidence"] = plan.confidence
    if plan.evidence_refs:
        runbook["evidence_refs"] = list(plan.evidence_refs)
    if plan.rationale:
        runbook["rationale"] = plan.rationale
    return runbook


def _dict_to_oracle(d: dict) -> Oracle:
    return Oracle(
        type=d["type"],
        port=d.get("port"),
        path=d.get("path"),
        command=d.get("command"),
        pattern=d.get("pattern"),
        acceptable_status=d.get("acceptable_status"),
    )


def _dict_to_component(d: dict) -> RunComponent:
    return RunComponent(
        name=d["name"],
        image=d.get("image"),
        role=d.get("role"),
        workdir=d.get("workdir"),
        command=d.get("command"),
        env=dict(d.get("env", {})),
        ports=list(d.get("ports", [])),
        depends_on=list(d.get("depends_on", [])),
        oracle=_dict_to_oracle(d["oracle"]) if d.get("oracle") else None,
    )


def _infer_shape(components: list[RunComponent]) -> RunShape:
    """Recover a shape from imported components.

    Order (see module docstring): component ``role`` first, then an *unambiguous*
    oracle via ``ORACLE_PRIMARY_SHAPE``, then structural (>1 component ->
    multi-component service), else plain service. Ambiguous oracles
    (``exit-zero``, ``http``) never decide shape on their own.
    """
    for comp in components:
        if comp.role in _ROLE_SHAPE:
            return _ROLE_SHAPE[comp.role]
    for comp in components:
        if comp.oracle is not None:
            shape = ORACLE_PRIMARY_SHAPE.get(comp.oracle.type)
            if shape is not None:
                return shape
    if len(components) > 1:
        return RunShape.MULTI_COMPONENT_SERVICE
    return RunShape.SERVICE


def _component_from_legacy(runbook: dict) -> RunComponent:
    """Synthesize a single component from a legacy runtime/steps/healthcheck."""
    runtime = runbook.get("runtime", {})
    start = runbook.get("steps", {}).get("start", [])
    first = start[0] if start else {}
    hc = runbook.get("healthcheck", {})
    oracle = Oracle(
        type="http",
        path=(hc.get("url_candidates") or [None])[0],
        acceptable_status=hc.get("acceptable_status"),
    )
    return RunComponent(
        name="app",
        image=runtime.get("image"),
        workdir=runtime.get("workdir"),
        command=first.get("command"),
        ports=list(first.get("expected_ports", [])),
        oracle=oracle,
    )


def runbook_to_plan(runbook: dict) -> RunPlan:
    """Import a v1 Runbook dict as a canonical ``RunPlan``.

    Uses ``components[]`` when present, else synthesizes one component from the
    legacy runtime/steps/healthcheck block. Shape is inferred conservatively via
    ``_infer_shape``.
    """
    raw_components = runbook.get("components")
    if raw_components:
        components = [_dict_to_component(c) for c in raw_components]
    else:
        components = [_component_from_legacy(runbook)]
    return RunPlan(
        id=runbook.get("id", ""),
        shape=_infer_shape(components),
        components=components,
        confidence=runbook.get("confidence"),
        evidence_refs=list(runbook.get("evidence_refs", [])),
        repo=runbook.get("repo"),
        rationale=runbook.get("rationale"),
    )
