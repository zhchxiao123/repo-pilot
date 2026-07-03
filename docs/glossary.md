# Glossary — GitHub Auto Runtime Testing System

Domain terms and their precise meanings. Kept in sync as design decisions are made.

| Term | Definition |
|------|------------|
| **Repository Profile** | Structured static-analysis result describing what a repo is (languages, frameworks, package managers, entrypoints, services, ports, env). Facts only — no chosen run command. |
| **Evidence** | A single traceable fact backing a conclusion: `{id, file, line, excerpt, kind, reason, confidence}` (`schemas/evidence.schema.json`). Every conclusion binds via `evidence_refs`. |
| **Run Shape** | The kind of runnable a repo is: `service`, `multi_component_service`, `cli`, `library`, `batch`, `build`, `docs` (not runnable), or `unknown`. Success is shape-specific (ADR-0019). |
| **Run Plan** | The canonical internal model of how a repo runs: `RunPlan(shape, components[], oracle)` (`repo_pilot/run_shape.py`). The source of truth the pipeline operates on; the v1 Runbook is its persisted projection (ADR-0019). |
| **Outcome** | The terminal shape-aware verdict: `verified` / `failed` / `deferred` / `not_runnable` / `no_candidate` / `error`, plus the shape it applies to (`repo_pilot/outcome.py`). A `cli` run to a clean exit is `verified`; a `docs` repo is `not_runnable`. |
| **Runbook Candidate** | A proposed, not-yet-verified way to run the project, with a confidence score and fallbacks. Multiple may exist per repo. |
| **Verified Runbook** | A Runbook Candidate that has actually started the project in the sandbox and passed a healthcheck. The system's core asset. |
| **Sandbox** | The one-shot isolated environment where untrusted repo commands execute. In v1, a job-scoped Docker Compose project (`compose.generated.yaml`) driven by the host CLI. |
| **`compose.generated.yaml`** | The compose project the system *synthesizes* for a job and actually runs. A **compiled artifact** derived from the Runbook, never hand-edited, regenerated every attempt. Never the repo's own compose file (that is only evidence). Allowlist-by-construction: non-root, `cap_drop: [ALL]`, resource limits, no host mounts/network/socket. |
| **`compile(runbook)`** | Deterministic, LLM-free function lowering a Runbook into `compose.generated.yaml`. The only place compose is produced. Pure → golden-file testable. |
| **Healthcheck** | The probe (HTTP status / port / log-ready) that decides whether a started project is actually usable. |
| **Repair Loop** | Bounded autonomous sub-graph that, on failure, diagnoses (rule-first, LLM fallback) and patches the *Runbook* (never the source) and retries. Bounds: ≤6/candidate, ≤2 no-progress, top-3 candidates, wall-clock backstop (ADR-0012). |
| **Progress ladder** | Monotonic stage order `setup < build < migrate < start < port_open < healthcheck_pass`. Repair "progress" = advancing the furthest stage reached — a sandbox fact, not the agent's opinion. |
| **Evidence Store** | Canonical append-only `evidence.jsonl` (ADR-0010); every fact lives here once with an `ev_*` id. Conclusions elsewhere carry `evidence_refs`. |
| **Test Target** | A discovered testable surface (HTTP API / Web UI / CLI) found after the app is running. |
| **Weak Oracle** | A correctness check applicable to any project (no 5xx, no crash, no stack-trace leak, valid JSON). |
| **Strong Oracle** | A correctness check derived from a spec source (OpenAPI schema, existing tests, README business rules). Schema-derived checks are **deterministic (Tier A)**; only business-intent ones are LLM (Tier B ②). |
| **Deterministic tool** | A no-LLM function agents call to produce ground-truth facts (parsers, `compile()`, healthcheck, confidence formula, §9.2 table, test runner). ADR-0004, `docs/determinism-boundary.md`. Golden-file testable. |
| **Agent-first orchestration** | Control flow is driven by LangGraph agents (ADR-0004/0005) with high autonomy; deterministic code is their toolbox, not a fixed pipeline. |
| **Subordination rule** | The invariant (ADR-0004): agents orchestrate and *propose*; truth is *adjudicated* only by cited evidence + sandbox execution. Every agent claim cites evidence or a sandbox result; tests bind to a discovered Test Target. |
| **Oracle (system)** | The source of ground truth: healthcheck for "did it start," OpenAPI/route parse for "does it exist," actual test run for "did it pass." Never the model's assertion. |
| **Macro-skeleton** | The fixed LangGraph DAG of phases: clone → profile → plan → verify → discover → test → report (ADR-0006). Edges are deterministic; each phase node is an autonomous agent. |
| **Phase-agent** | An autonomous agent/sub-graph implementing one phase with freedom *within* the phase. Transitions out are gated on tool/sandbox facts. |
| **Graph state** | The thin, typed LangGraph state with the Runbook as its spine: `repo_ref, profile, evidence[], runbook, attempts[], verified, targets[], tests[], report`. Slots hold references to artifacts, not blobs. |
