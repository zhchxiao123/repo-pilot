# Confidence Model (§7.1)

Deterministic Tier-A tool (ADR-0004). Computes a candidate Runbook's `confidence`
from its `evidence_refs` (ADR-0010). No LLM.

## Function

For a candidate backed by evidence items grouped into **distinct kinds** K:

1. **Per-kind reliability** `r(kind)` — likelihood the source alone implies the
   correct run method. Tunable against the §19 eval set:

   | kind | r |
   |---|---|
   | ci_step / ci_service | 0.85 |
   | compose_service / dockerfile / devcontainer | 0.80 |
   | readme_command | 0.70 |
   | package_script | 0.65 |
   | package_manager | 0.65 |
   | manifest_dependency | 0.60 |
   | entrypoint_inference | 0.45 |
   | llm_inference | 0.30 |

2. **Support** (noisy-OR across distinct kinds):
   `S = 1 − Π_{k ∈ K} (1 − r(k))`.
   Multiple items of the same kind count once → distinct *sources* drive
   independence; weak same-kind signals do not inflate.

3. **Conflict discount** — §7.2 rules mark contradicting evidence for the same
   decision. With `c = r(top_conflicting_kind)` (0 if none) and `κ = 0.5`:
   `confidence = S · (1 − κ · c)`.

4. Clamp to `[0, 1]`.

## Worked checks

- package_script + readme (`node_pnpm_dev`, §5.2): `1 − 0.35·0.30 = 0.90`.
- ci + readme + package_script: `1 − 0.15·0.30·0.35 ≈ 0.98`.
- readme-only, contradicted by lockfile: `0.70 · (1 − 0.5·0.65) = 0.47`.

## Separation of concerns

- §7.1's **priority list** governs *candidate generation order* (which to try
  first) — see §14.4 selection policy.
- This **formula** governs the *confidence score*.
- The reliability table and `κ` are hyperparameters, calibrated on §19, not fixed.
