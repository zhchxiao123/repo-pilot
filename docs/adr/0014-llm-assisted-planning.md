# ADR-0014 — LLM-assisted planning for stacks the rules don't cover

Status: accepted
Date: 2026-07-02

## Context

Deterministic planning (ADR-0004, `planner.py`) only builds candidates for stacks
with explicit rules — v1 that's Node (package.json entrypoints). Rules alone can't
cover every project (Python, Go, Rust, monorepos, unconventional setups), so those
repos produced no candidate. The earlier LLM seam was limited to extracting commands
from README prose. The goal: make the LLM a first-class *analysis* step for
uncovered stacks, without weakening the sandbox-as-oracle principle.

## Decision

Add **`llm_planner.propose_runbooks`**: when deterministic planning yields no
candidate (and the repo isn't compose-deferred), the LLM receives the profile,
evidence, and a compact view of the actual repo files (`gather_context`: file
listing + key-file snippets) and proposes 1–3 **full Runbook candidates** for any
stack. Proposals are schema-validated (`runbook.schema.json`); invalid ones are
dropped; the chosen one carries `llm_inference` confidence and is **still verified
by the sandbox** before being trusted. This supersedes the README-only
`nl_extract` seam (removed).

Gating and subordination (ADR-0004) hold: the LLM fires only as a fallback, the
proposal is schema-constrained, and the sandbox adjudicates truth. Compose-only
repos still defer (ADR-0002). A model error / missing key degrades to no candidate,
never a crash.

## Consequences

- The tool works on stacks with no deterministic rules (verified end-to-end on a
  Flask app: LLM plan → build → start → healthcheck → smoke 3/3, real Docker).
- Provider-agnostic via the model client (ADR-0005); token-free in tests via
  record/replay.
- This is the first of the planned LLM iterations; next is the LLM repair loop
  (ADR-0012) — the flagship agentic (conditional/cyclic) LangGraph use.
