# PRD — repo-pilot v1: Verified Runbook spine (depth-first, Node)

Labels: `ready-for-agent`

## Problem Statement

When I'm handed an unfamiliar GitHub project, the hard part isn't writing tests —
it's getting the thing to run at all. I don't know how to install it, what
services it needs, which command starts it, what env vars are required, or how to
tell whether it actually came up. Every unfamiliar repo is a manual, error-prone
archaeology session, and even AI coding tools assume the project already runs. I
want to point a tool at a repo and get back a *proven* way to run it plus a
first-pass verdict on whether it's healthy — without hand-holding, and without
trusting a machine that merely *claims* it worked.

## Solution

`repo-pilot` is a single-host CLI that takes a GitHub repo (and optional commit),
and turns it into a **Verified Runbook** — a structured, executed-and-proven
description of how to run the project — plus a smoke-test report. It clones the
repo, statically profiles it, plans candidate Runbooks from **evidence** (CI,
README, Dockerfile, manifests), compiles the best candidate into a generated Docker
Compose project, starts it in an isolated sandbox, and confirms it's actually up
via a healthcheck. If it comes up, it runs weak-oracle smoke tests and writes a
Markdown report with reproduce commands. If a step is a machine judgment call
(ambiguous prose, a novel failure), an agent proposes — but the **sandbox**, not
the agent, decides what's true.

v1 is **depth-first**: one repo shape — a single-service Node web app (Vite or
Express, no DB) — taken through the *entire* spine (clone → profile → plan → verify
→ discover → smoke test → report), proving the architecture end-to-end before
widening to more languages and capabilities.

## User Stories

1. As an operator, I want to run `repo-pilot run <github-url>` and get a
   Verified Runbook, so that I have a proven way to start an unfamiliar project.
2. As an operator, I want to pass an optional `--commit <sha>`, so that I can pin
   the analysis to an exact revision.
3. As an operator, I want the tool to clone the repo itself (shallow), so that I
   don't have to prepare a working copy.
4. As an operator, I want the languages, frameworks, and package managers detected
   automatically, so that I don't have to describe the stack.
5. As an operator, I want a single-service Node app (Vite or Express) to be
   recognized and started, so that the common case works out of the box.
6. As an operator, I want every conclusion in the Runbook to cite the evidence it
   came from, so that I can trust and audit *why* a command was chosen.
7. As an operator, I want candidate run methods scored by confidence, so that the
   most likely-correct one is tried first.
8. As an operator, I want CI workflow files mined for install/build/start commands,
   so that the tool prefers what the project actually runs.
9. As an operator, I want README install/run commands extracted, so that the tool
   uses documented steps when present.
10. As an operator, I want the tool to read package.json scripts and the lockfile,
    so that the right package manager and start script are used.
11. As an operator, I want the chosen Runbook compiled into a generated Compose
    project, so that starting the app is uniform whether it needs one container or
    several.
12. As an operator, I want the tool to run the app in an isolated sandbox, so that
    untrusted repo code can't touch my host or secrets.
13. As an operator, I want a real healthcheck (HTTP/port/log) to decide "it's up,"
    so that the verdict reflects reality, not a guess.
14. As an operator, I want a Verified Runbook artifact when startup succeeds, so
    that I have a reproducible, structured asset to reuse.
15. As an operator, I want a list of reproduce commands in the output, so that I
    can start the project myself without the tool.
16. As an operator, I want weak-oracle smoke tests run against the live app
    (`/`, `/health`, `/docs`, `/openapi.json`), so that I get a first-pass health
    verdict.
17. As an operator, I want smoke tests to flag 5xx, timeouts, invalid JSON, and
    leaked stack traces, so that obvious breakage is surfaced.
18. As an operator, I want a Markdown report summarizing detection, the Verified
    Runbook, and test results, so that I get a human-readable outcome.
19. As an operator, I want each failing test to include the request and a reproduce
    command, so that I can investigate quickly.
20. As an operator, I want a clear failure report when the project can't be started
    at all, so that I understand why rather than getting silence.
21. As an operator, I want public package downloads (pip/npm/etc.) to work inside
    the sandbox, so that installs succeed.
22. As an operator, I want private-network and cloud-metadata egress blocked by
    default, so that a malicious repo can't scan my LAN or steal cloud credentials.
23. As an operator, I want opt-out flags (`--allow-private-egress`,
    `--allow-metadata`, `--no-isolation`), so that I can consciously accept risk
    when a specific repo needs it.
24. As an operator, I want no real secrets ever injected and `.env.example` filled
    with dummy values, so that my credentials are never exposed to repo code.
25. As an operator, I want logs redacted of tokens/passwords/keys, so that stored
    artifacts don't leak secrets.
26. As an operator, I want CPU/memory/pids limits and a per-job timeout, so that a
    runaway or mining repo is bounded.
27. As an operator, I want all artifacts (profile, evidence, runbook, logs, report)
    written under a per-job directory, so that a run is self-contained and
    inspectable.
28. As an operator, I want the model provider to be swappable by config, so that I
    am not locked to one vendor.
29. As an operator, I want the tool to prefer deterministic evidence over LLM
    guesses, so that results are stable and cheap.
30. As an operator, I want an agent to step in only when deterministic extraction
    finds nothing (ambiguous README prose), so that odd projects still get a
    candidate without sacrificing determinism elsewhere.
31. As a maintainer, I want the Runbook to be the single source of truth with
    compose regenerated from it, so that what runs always matches the Runbook.
32. As a maintainer, I want the pipeline testable without Docker via a fake
    executor, so that CI is fast and deterministic.
33. As a maintainer, I want LLM calls record/replayed via fixtures, so that agent
    behavior is testable without live tokens.
34. As a maintainer, I want the compose compiler tested as a pure function, so that
    Runbook→compose lowering is verified by golden files.
35. As a maintainer, I want a small set of fixture repos (healthy Vite, healthy
    Express, a broken variant), so that the whole spine has deterministic
    end-to-end coverage.
36. As an operator, I want a repo whose only run path is its own compose file to be
    profiled and reported as "deferred", so that unsupported cases are explicit,
    not silent failures.

## Implementation Decisions

Grounded in ADR-0001..0012, `docs/determinism-boundary.md`,
`docs/confidence-model.md`, `docs/repair-loop.md`, and the JSON Schemas in
`schemas/`. Use the domain glossary (`docs/glossary.md`) vocabulary throughout.

**Delivery & runtime**
- v1 is a **single-host CLI** driving the **local Docker daemon**; no service,
  queue, or Kubernetes (ADR-0001). Core built as a transport-agnostic library so a
  service wrapper is additive later.
- Implemented in **Python** (ADR-0009).
- Depth-first scope: **one single-service Node app** (Vite/Express, no DB) through
  the full spine; widen by language/capability afterward (ADR-0008).

**Orchestration & intelligence**
- **Agent-first orchestration on LangGraph** with a **fixed macro-skeleton DAG**
  (clone → profile → plan → verify → discover → test → report) and **autonomous
  agents inside each phase**; phase transitions gate on tool/sandbox facts
  (ADR-0004, ADR-0006).
- **Thin, typed, Runbook-spine graph state**: `repo_ref, profile, evidence[],
  runbook, attempts[], verified, targets[], tests[], report`; large blobs live in
  the artifact store as references (ADR-0006).
- **Provider-agnostic model client** (Claude Opus 4.8 default, swappable by
  config); structured/schema-constrained output (ADR-0005).
- **Subordination rule**: agents orchestrate and *propose*; truth is adjudicated
  only by cited evidence + sandbox execution. Only three gated LLM seams exist —
  novel-failure repair, strong-oracle tests, NL-prose extraction — and each fires
  only when the deterministic path returns nothing (ADR-0004,
  `docs/determinism-boundary.md`). Strong-oracle tests are out of v1 scope (see
  Out of Scope), so v1 exercises at most the NL-extraction seam.

**Runbook & compilation**
- The **Runbook is the single source of truth**; a deterministic, LLM-free
  `compile(runbook) → compose.generated.yaml` is the only executor input and is
  regenerated every attempt (ADR-0003). Runbook conforms to
  `schemas/runbook.schema.json` — one artifact spanning `candidate → verified →
  failed` via `status`.
- **Compose is the sandbox substrate; we always generate our own** and never
  execute a repo's compose file verbatim (ADR-0002). A repo's compose file is
  evidence only. For v1's single-service Node scope this is a 1-service compose
  project.

**Evidence & confidence**
- **Evidence Store is canonical** (`evidence.jsonl`); Profile/Runbook conclusions
  carry `evidence_refs` (ADR-0010; `schemas/evidence.schema.json`,
  `schemas/profile.schema.json`).
- **Confidence** = noisy-OR over distinct evidence kinds × conflict discount, a
  deterministic tool (ADR-0011, `docs/confidence-model.md`). §7.1 priority list
  governs candidate *generation order*; the formula governs the *score*.

**Signal extraction (deterministic tools)**
- Profiler: file-tree scan, language detection, package.json/lockfile parsing,
  framework detection (Vite/Express signals for v1).
- Signal extractors for GitHub Actions, README command blocks, Dockerfile, package
  manifests — each emitting Evidence items.

**Sandbox & security**
- Sandbox Executor is the single Docker boundary. Every generated service carries,
  by construction: non-root user, `cap_drop: [ALL]`, resource limits, no host
  mounts/network/socket (ADR-0002/0007).
- **Default-safe security envelope with opt-out** (ADR-0007): public egress
  allowed; DROP to `10/8, 172.16/12, 192.168/16, 169.254/16`; no secrets, dummy
  `.env.example` values; log redaction; mandatory resource limits + job timeout;
  high-risk commands flagged not blocked. Flags: `--allow-private-egress`,
  `--allow-metadata`, `--no-isolation` (metadata block strongly recommended on).

**Verification, discovery, testing (v1 subset)**
- Healthcheck probes ordered candidate paths (`/health`, `/api/health`, `/docs`,
  `/openapi.json`, `/`) accepting non-5xx (§24.2); result written to the Runbook's
  `verification` block only from sandbox facts.
- Target Discovery v1: HTTP smoke targets only (fetch `/openapi.json` if present;
  otherwise the healthcheck paths).
- Test Generator v1: **weak-oracle smoke tests only** — templated, deterministic
  (no 5xx, no timeout, valid JSON when JSON, no stack-trace/secret leak).
- Report Writer: Markdown report covering project info, detection, Runbook
  candidates, verification, Verified Runbook, smoke targets, results, failures with
  reproduce commands (§14.6). Report inlines resolved evidence for humans.

**Repair Loop** — present as the cyclic sub-graph shape but **not required for the
first Node happy-path slice**; bounds and anti-thrash ladder per ADR-0012 /
`docs/repair-loop.md` land when the broken-repo slice is built.

## Testing Decisions

**What makes a good test**: assert only on **external behavior** at the confirmed
seams — the outputs of `run_job` over a fixture repo (produced Runbook /
Verified Runbook / report contents), the pure output of `compile(runbook)`, and
tool outputs — never on internal agent steps, prompt text, or graph node ordering.

**Seams (confirmed with the developer):**
1. **Primary behavioral seam — the job pipeline entry** (`run_job(repo_ref) →
   {runbook, verified_runbook, report}`), driven by small **fixture repos** checked
   into the suite (healthy Vite, healthy Express, a broken variant). Asserts on
   outputs: "Verified Runbook with healthcheck passed," "report lists failing smoke
   test + reproduce command," "unstartable repo yields a failure report."
2. **Sandbox Executor boundary** — an interface with a **real** implementation
   (integration tests, tagged slow, needs Docker) and a **fake** returning canned
   execution results, so planning/discovery/repair logic runs deterministically
   with no Docker. `compile(runbook)→compose` is a pure sub-seam with **golden-file
   tests**.
3. **Provider-agnostic model client boundary** — **record/replay fixtures** so the
   Tier-B agent(s) are testable without live tokens.

**Modules tested directly (pure/golden, not new seams):** profiler, each signal
extractor, the confidence formula (worked examples in `docs/confidence-model.md`
become test cases), conflict rules, healthcheck logic (against a fake HTTP server),
the weak-oracle smoke assertions.

**Prior art:** none yet — greenfield repo. These patterns (fixture-repo e2e, fake
adapter at the external boundary, golden-file for pure lowering, record/replay for
the model client) become the reference for later slices.

## Out of Scope

- Private repos; multi-service / microservice clusters; monorepos with multiple
  apps (single-service Node only for v1 — ADR-0008).
- Languages other than Node (Python/Java/Go come in later slices).
- Executing a repo's own compose file; Docker-in-Docker; testcontainers-based
  repos (ADR-0002).
- Service dependencies such as postgres/redis (later capability slice).
- The Repair Loop as a required happy-path capability (shape defined, ADR-0012;
  built in the broken-repo slice).
- Strong-oracle / business-flow tests; API contract tests from OpenAPI; UI
  (Playwright) tests (later slices — §11.3, §18 Phase 5).
- Service/API surface, Redis Streams, Kubernetes, multi-tenancy (ADR-0001).
- Stronger isolation (gVisor/Kata/Firecracker), egress proxy/allowlists, and
  command *blocking* (mid-term — §8.3, §20).
- Real third-party service credentials; automatic source-fixing PRs.

## Further Notes

- The one non-negotiable invariant across every slice: the **subordination rule** —
  agents may orchestrate and propose, but "did it start / does it exist / did the
  test pass" is decided by the sandbox and deterministic tools, never asserted by a
  model.
- Confidence reliability table and κ, and healthcheck acceptable-status sets, are
  **tunable hyperparameters** calibrated against an eval set (§19) in a later phase;
  v1 ships sensible defaults.
- The full decision record lives in `docs/adr/0001..0012`, with reference material
  in `docs/glossary.md`, `docs/determinism-boundary.md`, `docs/confidence-model.md`,
  and `docs/repair-loop.md`; schemas in `schemas/`.
- This PRD covers the depth-first v1 spine only; widening slices (languages, service
  deps, repair, richer tests) will be cut as follow-on issues by `to-issues`.
