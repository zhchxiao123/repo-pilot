# ADR-0011 — Confidence = noisy-OR over evidence kinds + conflict discount

Status: accepted
Date: 2026-07-01

## Context

§7.1 gives importance weights (summing to ~1.10) and a narrative ("not a simple
sum; independent+consistent evidence raises, conflicting lowers") but no function.
A literal weighted sum models neither independence nor conflict. Confidence is a
Tier-A deterministic tool (ADR-0004) and drives candidate ranking (§14.4).

## Decision

Adopt the model in `docs/confidence-model.md`:

- Per-kind reliability `r(kind)` (tunable), noisy-OR across **distinct kinds**
  `S = 1 − Π(1 − r(k))` — distinct sources drive independence, same-kind items
  count once.
- Conflict discount `confidence = S · (1 − 0.5 · r(top_conflicting_kind))`, using
  the §7.2 rules to identify contradictions.
- Clamp `[0,1]`.

Also settles a doc ambiguity: §7.1's priority *list* governs candidate
**generation order**; this formula governs the **score**. They are separate jobs.

## Consequences

- Deterministic, monotonic, bounded, explainable; golden-file testable.
- Reliability table and κ are hyperparameters calibrated on the §19 eval set — not
  sacred; expect tuning once real repos are measured.
- Requires the §7.2 conflict rules as a prerequisite tool to detect contradictions.
