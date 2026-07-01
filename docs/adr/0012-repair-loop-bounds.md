# ADR-0012 — Repair Loop bounds and anti-thrash rule

Status: accepted
Date: 2026-07-01

## Context

Repair is an autonomous cyclic sub-graph (ADR-0006), rule-first with LLM fallback
(ADR-0004), editing only the Runbook (ADR-0003). §9 requires bounded attempts and
a `risk_too_high` gate but pins no numbers or anti-thrash rule. An autonomous agent
can burn attempts looping on variations of a broken idea; it needs hard stops and a
sandbox-grounded progress definition.

## Decision

Adopt `docs/repair-loop.md`:

- **Progress ladder** `setup < build < migrate < start < port_open <
  healthcheck_pass`; progress = advancing the furthest stage reached.
- **Bounds**: ≤ 6 attempts/candidate; ≤ 2 consecutive non-progress → abandon
  candidate; top-3 candidates by confidence; job wall-clock backstop (default
  ~20 min, configurable).
- **No-repeat**: reject already-tried patch hashes without a sandbox run.
- **Diagnosis order**: §9.2 rule table first; Tier-B LLM only when unmatched;
  record `source: rule|llm`.
- **Risk gate**: auto-reject patches weakening the ADR-0007 envelope
  (`--privileged`, host mounts, disabling egress, real secrets). Safety is never
  traded for a green healthcheck.
- **Acceptance**: keep a patch only if sandbox re-run advances the ladder; else
  discard + recompile.

## Realization (2026-07-02)

Implemented as a **cyclic LangGraph** (the framework's first real use): `verify`
branches via conditional edges to `discover` (verified) / `repair` (retry) /
`report` (give up); `repair` loops back to `verify`. `repair.py` does rules-first
(§9.2 patterns: corepack/pip/npm) then LLM patching, schema-validated, security
preserved. Bounds: `max_repair_attempts` (default 3) + no-repeat patch fingerprint.
Verified end-to-end in real Docker: a broken plan → LLM repair → re-verify → smoke.
The full progress-ladder (setup<build<…) is a later refinement.

## Consequences

- Thrash caught early by 2-consecutive-no-progress before the hard cap.
- Worst case (6 × 3 candidates × sandbox time) bounded by the wall-clock backstop.
- Progress and acceptance are sandbox facts, upholding the subordination rule.
- Requires the §9.2 rule table and a normalized Runbook-diff hash as prerequisite
  tools.
