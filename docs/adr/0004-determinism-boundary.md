# ADR-0004 — Agent-first orchestration, evidence-grounded truth

Status: accepted (revised 2026-07-01, same day — superseded the initial
"deterministic-by-default pipeline" framing before any code existed)

Date: 2026-07-01

## Context

§13 lists 11 "Agents"; §4.1 demands evidence-first, not LLM-first. An initial
draft of this ADR made the *whole pipeline* deterministic code with three gated
LLM seams. The team chose instead to build an **agent-first / LangGraph-oriented**
system (high-autonomy orchestration, easy model swap — see ADR-0005).

The reversal was stress-tested against the doc's most-repeated principle (§4.1,
§2.2, §4.3, §11.4, §27, §25 all reject LLM-first *decision-making*). The
resolution separates two layers that "LLM-first" was bundling:

- **Orchestration** — who drives control flow. Agent-first is fine here.
- **Truth-grounding** — who decides what is *real*. Must stay evidence + sandbox,
  or a *verification* product becomes "the agent believes it works," which is
  worthless.

The team selected **Option A: agent-first orchestration, evidence-grounded truth.**

## Decision

**Orchestration is agent-first.** Agents (LangGraph graphs, ADR-0005) are the
primary units that drive profiling, planning, repair, and test generation. They
decide what to do next and call tools freely with high autonomy.

**Deterministic code is demoted from "the pipeline" to "tools the agents call"** —
manifest/CI/Dockerfile parsers, framework detection, the `compile(runbook)→compose`
function (ADR-0003), healthcheck probes, the confidence formula (§7.1), the §7.2
conflict rules, the §9.2 known-failure table, OpenAPI/route/DOM extraction,
weak-oracle and schema-derived test generators, and the test-DSL runner. These are
the agents' toolbox, not a fixed rail.

**The oracle does not move — the subordination rule binds under any architecture:**

1. Truth = **cited evidence + sandbox execution**, never an agent's assertion.
   "Did it start?" is decided by the healthcheck; "does this endpoint exist?" by
   the OpenAPI/route parse; "did the test pass?" by the test actually running.
2. Every agent claim must cite evidence (the Evidence model, §4.1) or a sandbox
   result.
3. Generated tests must bind to a discovered Test Target (§11.4) — no fabrication.

## Consequences

- The team gets the agent-first architecture it wants; the doc's thesis (§1/§4/§27)
  survives because agents orchestrate but do not adjudicate truth.
- Deterministic tools remain first-class and golden-file testable; they are now
  invoked by agents rather than by a hard-coded `run_job`.
- Risk to watch: agent autonomy must not be allowed to *assert* success/existence
  without an evidence or sandbox citation. Enforced by making tool outputs the only
  source of ground-truth facts in the state, and by validating test targets before
  test execution.
- `docs/determinism-boundary.md` reframed accordingly: Tier A = deterministic
  *tools*; agent orchestration sits above them; the subordination rule is the
  invariant.
