# 0020 — AI agent is the primary consumer; the artifact is a reusable bring-up contract

Status: accepted

## Context

v1 framed repo-pilot's output as a human-readable report plus a Verified
Runbook. A direction review (2026-07-05) sharpened who the output is really
for: **an AI coding agent that needs end-to-end verification while editing a
repository it did not write**. The agent's loop is:

1. repo-pilot analyzes the repo **once** and emits a verified artifact bundle.
2. The agent edits code, then **re-runs the bundle against its own modified
   working tree** — up, readiness, pass/fail — as many times as it wants,
   with repo-pilot itself no longer in the loop.

The same review drew a hard scope boundary: downstream verification (UI
screenshot tests, richer functional/E2E suites) is the *consumer's* job.
**repo-pilot's job ends at bringing the project up and proving it is up.**
What downstream tools need from us is not tests but a machine-readable
contract: how to start the system, how to know it is ready, and how to reach
it.

## Decision

**The primary consumer of repo-pilot's output is a machine.** The artifact
bundle (`runbook.yaml` + `compose.generated.yaml`, per ADR-0003) is a
**reusable bring-up contract**; `report.md` is the explanation layer for
humans.

**Scope boundary: bring-up, not downstream testing.** repo-pilot discovers,
verifies, and hands over *how to run the repo*. Light smoke probing stays —
it is how we prove a shape is exercisable (ADR-0018/0019) — but repo-pilot
does not generate or own functional test suites, UI tests, or screenshot
comparisons. Those belong to the consumer, attached on top of the contract.

Three constraints bind all coverage work (compose import, Dockerfile-first,
monorepo, new ecosystems) so that breadth never regresses reuse:

1. **Re-runnable against a modified tree.** `compose.generated.yaml` must
   always work when re-executed against a *changed* working tree: repo-code
   components mount/build writable (landed in PRs #60/#61), no baked-in
   assumptions that only hold for the pristine pinned clone. Guarded by
   regression tests whenever executor/compose build behavior changes.
2. **Reach and readiness are machine-readable.** Whatever verification
   discovers — published ports, base URLs, readiness oracle (healthcheck,
   log signal, exit code), per-component roles — lands in structured fields
   of `runbook.yaml` (or a sibling structured artifact), never only as prose
   in `report.md`. A downstream tool must be able to attach (e.g., point a
   browser at the service) without parsing markdown.
3. **Converge on a single-command entry.** Reproduce instructions trend
   toward "one command = bring up + readiness check + meaningful exit code",
   so an agent can eventually invoke verification as a single step.

**Priority is unchanged.** The coverage-driven expansion plan
(`docs/plans/2026-07-04-coverage-driven-runtime-expansion.md`) proceeds as
written; a dedicated reuse executor (`repo-pilot replay` / `verify.sh`) is
deliberately deferred until eval milestone M2 (≥50% correct verdicts on the
50-case manifest) proves the coverage base, then gets scoped from eval data.

## Consequences

- Coverage phases carry the three constraints as acceptance criteria; a
  capability that verifies but emits a non-reusable or prose-only artifact
  is not done.
- The Runbook schema grows toward the contract (reach/readiness fields)
  additively, consistent with the v2 projection strategy in ADR-0019.
- Smoke/target discovery output is persisted as structured data when that
  work happens (plan Phase 6), so the future reuse executor only adds an
  entry point — no retrofitting.
- Feature ideas that amount to "repo-pilot runs the project's UI tests /
  generates test suites" are out of scope by default and need a new ADR to
  overturn this boundary.
