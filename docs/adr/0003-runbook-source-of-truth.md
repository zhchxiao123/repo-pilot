# ADR-0003 — Runbook is source of truth; compose is a compiled artifact

Status: accepted
Date: 2026-07-01

## Context

§4.2 makes the Runbook the core asset and "interface protocol for everything
downstream." ADR-0002 introduced `compose.generated.yaml` as the thing actually
executed. Two structured "how to run" artifacts now exist; §9.3 requires the
Repair Loop to only edit the Runbook. Ambiguity between the two would make repair
and verification incoherent.

## Decision

The **Runbook** (the §7.3 YAML) is the single source of truth — the only artifact
planners produce and the Repair Loop mutates. `compose.generated.yaml` is a pure
compilation target produced by a deterministic, LLM-free `compile(runbook) →
compose` function, regenerated on every attempt and never hand-edited.

The Repair Loop patches the Runbook, then recompiles. There is no API to edit
compose directly, so §9.3's "repair only edits the Runbook" holds by construction.
Healthcheck, resource limits, security flags, and service wiring live in the
Runbook schema and are lowered into compose fields by the compiler.

## Consequences

- One artifact to reason about, diff, verify, and store (§15.2 `verified-runbook.yaml`);
  compose is a derived build artifact like an object file.
- `compile()` is a pure function → golden-file tests (Runbook in → compose out)
  with no Docker required.
- Repair cannot persist a change not reflected in the Runbook; the Runbook always
  fully explains the running system.
- Constraint: anything runnable must be expressible in the Runbook schema. New
  compose features require extending the schema + compiler first — no raw-compose
  escape hatch. This deliberately keeps the surface small.
