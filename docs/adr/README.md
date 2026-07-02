# Architecture Decision Records

Decisions locked down while grilling `docs/github_auto_runtime_testing_plan.md`.

Each ADR: Context → Decision → Consequences. Status: proposed | accepted | superseded.

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-v1-runtime-host.md) | v1 runtime host: single-host local Docker CLI | accepted |
| [0002](0002-compose-as-sandbox-substrate.md) | Docker Compose as sandbox substrate; always generate our own | accepted |
| [0003](0003-runbook-source-of-truth.md) | Runbook is source of truth; compose is a compiled artifact | accepted |
| [0004](0004-determinism-boundary.md) | Agent-first orchestration, evidence-grounded truth | accepted |
| [0005](0005-langgraph-and-model-portability.md) | LangGraph orchestration + provider-agnostic model client | accepted |
| [0006](0006-agent-topology.md) | Agent topology: fixed macro-skeleton + autonomous phase-agents | accepted |
| [0007](0007-v1-security-envelope.md) | v1 security envelope: default-safe with opt-out | accepted |
| [0008](0008-depth-first-v1-milestone.md) | Depth-first v1: one repo type through the full spine | accepted |
| [0009](0009-implementation-language-python.md) | Implementation language: Python | accepted |
| [0010](0010-evidence-store-canonical.md) | Evidence Store canonical; conclusions carry evidence_refs | accepted |
| [0011](0011-confidence-model.md) | Confidence = noisy-OR over evidence kinds + conflict discount | accepted |
| [0012](0012-repair-loop-bounds.md) | Repair Loop bounds and anti-thrash rule | accepted |
| [0013](0013-executor-for-isolated-daemons.md) | Executor for isolated Docker daemons: build-context + in-network probe | accepted |
| [0014](0014-llm-assisted-planning.md) | LLM-assisted planning for stacks the rules don't cover | accepted |
| [0015](0015-llm-assisted-profiling.md) | LLM-assisted profiling for unrecognized stacks | accepted |
