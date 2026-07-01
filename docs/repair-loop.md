# Repair Loop (§9)

An autonomous cyclic sub-graph (ADR-0006), rule-first with LLM fallback
(ADR-0004), editing **only the Runbook** (ADR-0003). Bounds per ADR-0012.

## Progress ladder

Monotonic stage order — the sandbox-grounded progress signal (not the agent's
opinion):

```
setup < build < migrate < start < port_open < healthcheck_pass
```

An attempt *makes progress* only if it advances the **furthest stage ever reached**
for the candidate.

## Bounds

| Bound | Value |
|---|---|
| Attempts per candidate | ≤ 6 (hard cap) |
| Consecutive non-progress attempts | ≤ 2 → abandon candidate early |
| Candidates tried (confidence order) | top 3 |
| Job wall-clock budget | configurable, default ~20 min (backstop) |

## Rules

1. **No-repeat** — hash each proposed patch as a normalized Runbook diff; a
   previously-tried hash is rejected without a sandbox run.
2. **Diagnosis order** — §9.2 rule table first (deterministic, free); Tier-B LLM
   proposes only when no rule matches. Each attempt records `source: rule|llm` in
   `repair_history[]`.
3. **Risk gate** — high-risk patches auto-rejected in v1: anything weakening the
   ADR-0007 envelope (`--privileged`, host mounts, disabling egress, real secrets).
   Repair may never trade safety for a green healthcheck.
4. **Acceptance** — a patch is kept only if sandbox re-run advances the ladder;
   otherwise discarded and recompiled (Runbook is source of truth).

## Loop shape (pseudocode, cf. §9.1)

```
for candidate in top_3_by_confidence:
    reached = NONE; no_progress = 0; tried = set()
    for attempt in 1..6:
        result = sandbox.run(compile(runbook))
        if result.healthcheck_passed: return Verified(runbook, result)
        diag = rule_diagnose(result) or llm_diagnose(result)   # rule first
        patch = propose(diag, runbook)
        if patch is None or patch.risk_high or hash(patch) in tried: break
        tried.add(hash(patch)); runbook' = apply(runbook, patch)
        if stage(result) > reached: reached = stage(result); no_progress = 0
        else: no_progress += 1
        if no_progress >= 2: break
        runbook = runbook'   # kept only because ladder advanced or re-check pending
    # exhausted -> next candidate
return FailedRunbook(diagnosis)
```
