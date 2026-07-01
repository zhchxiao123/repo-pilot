# ADR-0010 — Evidence Store canonical; conclusions carry evidence_refs

Status: accepted
Date: 2026-07-01

## Context

The doc contradicts itself on evidence: §5.1/§7.3 embed `evidence: []` inline on
the Runbook/Profile, while §14.3 defines a separate Evidence Store with ids. Both
cannot be the source of truth. The choice affects the whole schema shape and how
confidence aggregation (§7.1) and repair reason over facts.

## Decision

A separate **Evidence Store is canonical**. Each fact lives once as
`{id, file, line, kind, excerpt, reason, confidence}` in an append-only
`evidence.jsonl` (§15.2). Profile fields, Runbook conclusions, and candidate
confidence carry **`evidence_refs: ["ev_001", ...]`** — references, not embedded
copies.

## Consequences

- No duplication/drift: one identity per fact.
- Enables §7.1 independence detection — confidence aggregation can tell whether two
  conclusions rest on the *same* evidence (not independent) or different evidence.
- Repair patches stay small (reference ids, not fact payloads).
- The machine-facing Runbook is normalized (not standalone-readable); the human
  **report** (§14.6) and `reproduce` block inline resolved evidence.
