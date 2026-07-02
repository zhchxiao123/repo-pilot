# ADR-0015 — LLM-assisted profiling for unrecognized stacks

Status: accepted
Date: 2026-07-02

## Context

Deterministic profiling (`profiler.py`) only recognizes stacks with explicit rules
(v1: Node via package.json). For anything else it returns a thin Profile (no
framework, no entrypoint), which weakens the report and gives the LLM planner
(ADR-0014) little structured context. LLM-assisted planning already covers "how to
run it"; this covers "what is it" for the same uncovered stacks.

## Decision

Add **`llm_profiler.enrich_profile`**: in the profile phase, when the deterministic
Profile is thin (no `frameworks` and no `entrypoints`) and a model client is
present, the LLM reads the repo files (`gather_context`) and returns structured
signals — languages, frameworks, **required services** (postgres/redis/…),
**required env vars**, and ports — which are merged into the Profile and backed by
one `llm_inference` evidence item (`ev_pl1`). The enriched Profile is
schema-validated and feeds the planner.

Gating and subordination (ADR-0004) hold: it fires only when rules found nothing,
is schema-constrained, is analysis-only (the sandbox still decides whether anything
runs), and no-ops / degrades on empty or malformed output.

## Consequences

- Reports show a detected stack (and its service/env needs) even for repos the
  rules don't recognize; the LLM planner gets richer input.
- Two LLM calls for an unrecognized stack (enrich, then plan) — acceptable; both
  gated and token-free in tests via record/replay.
- Surfaces required **services** — the input a future service-dependency capability
  and the repair loop can act on.
- Verified end-to-end in real Docker (Flask: enrich → plan → build → verify →
  smoke 3/3).
