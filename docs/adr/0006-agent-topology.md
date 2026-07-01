# ADR-0006 — Agent topology: fixed macro-skeleton + autonomous phase-agents

Status: accepted
Date: 2026-07-01

## Context

Per ADR-0004/0005 the system is agent-first on LangGraph. The §13 "11 agents" must
become a concrete graph shape. Options: (1) one monolithic flat graph, (2) a free
supervisor choosing agents at will, (3) a fixed macro-skeleton with autonomous
agents inside each phase.

The macro order — clone → profile → plan → verify → discover → test → report — is a
genuine DAG: profiling needs a clone, verification needs a Runbook, target
discovery needs a running app, testing needs targets. That order is a fact, not a
decision.

## Decision

**Topology #3.** The macro-flow is a fixed LangGraph skeleton encoding the DAG
above with deterministic edges. Each phase node is itself an autonomous agent /
sub-graph with full freedom *within* its phase. The Repair Loop is a cyclic
sub-graph. Phase transitions are gated on **tool/sandbox facts** (e.g. healthcheck
passed → advance to discovery), never on an agent asserting "looks good."

**State is thin, typed, Runbook-spine.** The LangGraph state carries typed slots:
`repo_ref`, `profile`, `evidence[]`, `runbook` (source of truth, ADR-0003),
`attempts[]`, `verified`, `targets[]`, `tests[]`, `report`. Slots are populated
only by a tool or a schema-validated agent output. Large artifacts (logs, traces,
screenshots) live in the artifact store (§15.2); state holds references, not
contents.

## Consequences

- Autonomy is spent where it pays (planning over conflicting evidence, multi-step
  repair, test synthesis), not on re-deriving a fixed phase order.
- Subordination rule (ADR-0004) stays enforceable: transitions gate on facts.
- Every state slot traces to a tool result → "what's true" is always grounded.
- Adding a genuinely new *phase* is a code change (edit the graph), not something
  an agent improvises. Intended — §18 roadmap phases are known in advance.
