# ADR-0008 — Depth-first v1: one repo type through the full spine

Status: accepted
Date: 2026-07-01

## Context

§18 builds breadth-first by phase (all static analysis for Node/Python/Java/Go →
then sandbox verify → then smoke tests). §26's "minimal version" is a thin
end-to-end. The next step (`to-issues`) slices work as tracer-bullet vertical
slices, which favors a vertical first cut. The biggest unproven risk is whether the
agent-first spine (ADR-0004/0006) can take a real repo to a Verified Runbook.

## Decision

**Depth-first.** v1's first milestone takes **one repo type through the entire
macro-skeleton** before widening:

- **First shape: a single-service Node web app** (Vite or Express, no DB) — least
  incidental complexity, exercises every phase.
- **Full spine, clone → report**: clone → profile → plan Runbook → `compile→compose`
  → sandbox verify (real healthcheck) → genuine **Verified Runbook** → weak-oracle
  smoke test → `report.md`.
- **Then widen by language**: Python (FastAPI/Django) → Java (Spring Boot) → Go —
  each reusing the spine, adding only profiler rules + runtime images.
- **Then widen by capability**: a service dependency (postgres) → Repair Loop →
  schema-derived API tests.

The Repair Loop is **out** of the first slice (a §18 Phase 3 capability); its bounds
get a dedicated ADR when reached.

## Consequences

- Proves the hardest integration risk in week one on the easiest repo; every later
  slice has a working Verified Runbook to reference (§4.2).
- Maps cleanly onto tracer-bullet vertical slicing for `to-issues`.
- Trade-off: the tool only handles Node until languages 2–4 land — a working narrow
  spine over four half-built horizontal layers, by choice.
