# Grounding Boundary — agent orchestration vs. the oracle

Reframed per ADR-0004 (agent-first orchestration, evidence-grounded truth).

The system is **agent-first in orchestration** and **evidence-grounded in truth**.
Two layers, kept distinct:

- **Orchestration** — agents (LangGraph, ADR-0005) drive control flow with high
  autonomy and call deterministic tools freely.
- **Truth-grounding** — what is *real* is decided by cited evidence + sandbox
  execution, never by an agent's assertion.

## The subordination rule (the invariant)

Under any architecture:

1. **Truth = evidence + sandbox.** "Did it start?" → healthcheck. "Does this
   endpoint exist?" → OpenAPI/route parse. "Did the test pass?" → the test actually
   running. Not the model's word.
2. **Every agent claim cites** an Evidence item (§4.1) or a sandbox result.
3. **Generated tests bind to a discovered Test Target** (§11.4) — no fabrication.

Non-determinism is allowed in *orchestrating and proposing*; it is never allowed in
*adjudicating truth*.

## Deterministic tools (the agents' toolbox)

Agents orchestrate; these tools produce the ground-truth facts. Each is
golden-file testable with no live tokens.

| Tool | Mechanism |
|---|---|
| File-tree scan, language detection | extensions + linguist-style rules |
| Manifest/lockfile parsing (package.json, pyproject, go.mod, Cargo, pom) | format parsers |
| CI workflow parsing (`.github/workflows`) | YAML parse; `setup-node@v4 → node` lookup |
| Dockerfile parsing | instruction grammar |
| Framework detection (§6.6) | dependency-presence + file-signal rules |
| Confidence aggregation (§7.1) | weighted, independence-adjusted formula |
| Conflict resolution (§7.2) | the §7.2 rule table |
| `compile(runbook) → compose` (ADR-0003) | pure function |
| Command execution, port/HTTP healthcheck (§24.2) | pure I/O |
| Log capture + secret redaction | regex |
| Known-failure diagnosis + repair (§9.2 table) | stderr string-match → tabled patch |
| OpenAPI/route parsing, DOM crawl | schema + AST + Playwright DOM |
| Weak-oracle tests (§11.2) | templated probes |
| Schema-derived API tests (§11.3, from OpenAPI) | schemathesis-style generation |
| Test execution (DSL runner) | mechanical |

## Where LLM/agent autonomy adds value (orchestration + proposing)

- **LLM-assisted planning** (ADR-0014): when deterministic rules yield no candidate
  (a stack rules don't cover), propose full Runbook candidates from profile +
  evidence + repo files. Schema-constrained; sandbox-verified.
- Interpreting **novel** failure logs into a proposed Runbook patch (repair loop).
- Synthesizing **strong-oracle** business-intent tests bound to Test Targets.

In all four, the agent *proposes*; a deterministic tool or the sandbox *verifies*
before the result is trusted.
