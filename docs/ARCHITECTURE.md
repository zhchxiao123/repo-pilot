# Architecture

How `repo-pilot` is put together, the principles it holds to, and where the seams
are. The authoritative design decisions live as ADRs in [adr/](adr/README.md);
this document is the map.

## Guiding principles

1. **Evidence-first, not LLM-first.** Run steps come from real signals (CI, README,
   Dockerfile, manifests), each bound to traceable evidence. (ADR-0004)
2. **The sandbox is the oracle.** "Did it start? Does this endpoint exist? Did the
   test pass?" are decided by actual execution, never by a model's assertion. Agents
   may *orchestrate and propose*; only deterministic tools + the sandbox *adjudicate*.
   (ADR-0004, `determinism-boundary.md`)
3. **The Runbook is the source of truth.** Everything downstream is derived from it;
   the compose project is a compiled artifact, regenerated each run. (ADR-0003)
4. **Default-untrusted.** Repo code runs in a one-shot hardened container. (ADR-0007)

## The pipeline (macro-skeleton)

A LangGraph graph (ADR-0006). Each phase reads/writes a thin, typed state whose
spine is the Runbook. The happy path is linear; `verify` uses **conditional +
cyclic edges** for the repair loop (ADR-0012).

```
clone → profile → plan → verify ─▶ discover → test → report
                            │  ▲
                            ▼  │ (patch & retry)
                          repair
```

| Phase | Module | Does |
|-------|--------|------|
| clone | `cloner.py` | shallow clone + optional commit checkout → `RepoRef` |
| profile | `profiler.py`, `extractors.py`, `evidence.py` | detect language/framework/pkg-mgr and extract CI/README/Dockerfile/compose signals → Profile + Evidence Store |
| plan | `planner.py`, `plan_agent.py`, `explore_tools.py` | recognized stacks: deterministic candidates ranked by confidence. Otherwise the **plan agent** explores the repo with read-only tools, classifies it, and proposes ranked Runbooks (ADR-0016). Compose-only repos defer. |
| verify | `executor.py`, `compose.py`, `healthcheck.py` | compile Runbook → compose, run it in the sandbox, healthcheck it |
| discover | `discovery.py` | find HTTP test targets (OpenAPI, else healthcheck paths) |
| test | `smoke.py` | run weak-oracle smoke tests against the live app |
| report | `report.py` | render `report.md`; persist the Verified Runbook |

Orchestration and glue: `graph.py` (the DAG + node wiring), `cli.py` (entry point),
`config.py` (config + swappable model provider), `artifacts.py` (per-job layout),
`schemas.py` (validation), `security.py` (envelope), `model_client.py` /
`nl_extract.py` (the gated LLM seam).

## The determinism boundary

Almost everything is deterministic and runs with **no LLM and no tokens**: parsing,
framework detection, the confidence formula, compose compilation, execution,
healthcheck, discovery, and smoke tests. The LLM is a *gated fallback* — chiefly
**LLM-assisted planning** (ADR-0014): when deterministic rules produce no candidate
(a stack rules don't cover), the model proposes full Runbook candidates from the
profile + evidence + repo files, and the **sandbox still verifies** them. Details in
[`determinism-boundary.md`](determinism-boundary.md); the confidence math in
[`confidence-model.md`](confidence-model.md); the (planned) repair loop in
[`repair-loop.md`](repair-loop.md).

## The sandbox

The `SandboxExecutor` is the single boundary that touches Docker and the network
(ADR-0002). The real `DockerSandboxExecutor`:

- **copies the repo into an image** via a generated Dockerfile + compose `build:`
  (works even when the daemon doesn't share the host filesystem — ADR-0013);
- emits per-service hardening (non-root default, `cap_drop: ALL`,
  `no-new-privileges`, resource limits);
- **probes HTTP from a throwaway container** so it reaches daemon-side published
  ports (ADR-0013).

A `FakeSandboxExecutor` implements the same interface with canned results, so the
whole pipeline is testable without Docker.

## Test seams

Three seams keep the system testable (the two external non-determinisms are Docker
and the LLM; everything else funnels through the top):

1. **Pipeline entry** — drive `build_graph(...).invoke(initial_state(...))` over
   small **fixture repos** (`tests/fixtures/repos/`), asserting on outputs.
2. **Sandbox Executor** — real (integration, Docker) vs fake (unit). `compile()` is
   a pure sub-seam with golden-file tests.
3. **Model client** — record/replay, so the LLM seam is testable without tokens.

Run unit tests with `pytest -q`; integration with
`REPO_PILOT_COMPOSE_CMD="sudo docker compose" pytest -m integration -o addopts=""`.

## Schemas

Structured contracts in [`../schemas/`](../schemas/):
`runbook.schema.json`, `profile.schema.json`, `evidence.schema.json`.

## Roadmap

Delivered v1 (depth-first, Node): clone→report spine, evidence-based planning,
real Docker verify, discovery, weak-oracle smoke, security envelope, LLM fallback.

Next:
- More languages: Python (FastAPI/Django), Java (Spring Boot), Go.
- Service dependencies (postgres/redis) as sibling compose services.
- The **repair loop** (ADR-0012) — auto-diagnose + patch the Runbook on failure.
- Strong-oracle / OpenAPI-contract / Playwright UI tests.
- **Egress enforcement** (apply the computed policy as host firewall rules).
- Service/API surface + queue for concurrent, remote jobs (ADR-0001 defers this).

## Decision records

See [adr/README.md](adr/README.md) for ADR-0001 through ADR-0013.
