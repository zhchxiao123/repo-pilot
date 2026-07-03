# 0019 — Run Shape is the primary model

Status: accepted

## Context

ADR-0018 established that a repo is a Run Plan of N components, each with a
readiness oracle. But in the code that model was still an *extension* on top of
the single-service v1 Runbook: `components[]` rode alongside `runtime`/`steps`/
`healthcheck`, and callers branched on `if runbook.get("components")`. The mental
centre was still "an HTTP service", with everything else bolted on.

repo-pilot's actual job is broader: **discover, express, verify, and report the
most appropriate runnable shape of a repository** — service, multi-component
service, cli, library, batch, build, or docs (not runnable). Single-service HTTP
is one shape, not the whole product.

## Decision

**The canonical internal model is `RunPlan(shape, components[], oracle)`**
(`repo_pilot/run_shape.py`). Planning, verification, outcome, reporting, and
evaluation operate on this model. The v1 Runbook remains the **persisted
compatibility artifact**; `runbook_projection.py` is the only place that converts
between the two.

Supporting decisions:

- **One shape→oracle source of truth.** `SHAPE_ORACLES` (and its partial inverse
  `ORACLE_PRIMARY_SHAPE`) fixes which oracles are valid for which shape.
  Detection, projection, and planning all consult it, so they cannot disagree.
  Ambiguous oracles (`exit-zero`, `http`) never infer shape on their own — the
  component `role` does. This is what lets projection round-trip (a `batch` plan
  survives v1 and back instead of degrading to `service`).
- **One verifier interface.** `run_verifier.verify_run_plan(plan, executor,
  repo_dir)` owns single-app vs multi-component adjudication; callers no longer
  branch on `components`.
- **Shape is not verdict.** `cli`/`library`/`build`/`batch` are shapes that can
  verify successfully by being *exercised*; `docs` is honestly `not_runnable`.
  Terminal results are a shape-aware `Outcome` (`outcome.py`), and eval scores a
  compound `kind:shape` verdict with per-shape coverage.
- **LLM proposes, sandbox adjudicates** (ADR-0004 unchanged): the model may
  classify and propose plans, but success comes only from oracle execution.

## Consequences

- New code consumes `RunPlan`, not raw runbook dicts. Persisted `runbook.yaml`
  stays v1 until a CLI migration story exists; a v2 `run-plan.schema.json` exists
  additively (shape/components/oracle/outcome first-class, no required
  runtime/steps/healthcheck) without disturbing v1 validation.
- A repo's verified result is described as a shape-specific outcome, and reports
  explain *what ran, how it was verified, and how to reproduce it* at
  shape-appropriate granularity (component command vs `docker compose up`).
- Migration is staged (strangler-fig): the canonical modules and the projection
  landed first and are covered; the graph's full adoption of `plan_candidates` +
  a unified `verify_run_plan` path is a subsequent step, deliberately separated
  from the extraction so it does not mix with it.
