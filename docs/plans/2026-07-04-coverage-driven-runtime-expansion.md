# Coverage-Driven Runtime Expansion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move repo-pilot from a correct core Verified Runbook engine to a coverage-driven tool that can discover, verify, and report reproducible run methods for a much wider set of real repositories.

**Architecture:** Keep the current `RunPlan -> verify_run_plan -> Outcome -> report` spine intact. Expand capability through measured vertical slices: eval first, then compose-native import, Dockerfile-first planning, monorepo/ecosystem support, and stronger report explanations. Every new capability must start with eval cases and end with a verified or honestly deferred outcome.

**Tech Stack:** Python 3.11+, pytest, dataclasses, PyYAML, Docker Compose, LangGraph, LangChain tool-calling seam, JSON/Markdown artifacts.

---

## Product Target

repo-pilot's purpose is not to analyze a repository for its own sake. It analyzes a repository in order to find and verify a reproducible way to run or exercise it.

The desired product loop is:

```text
target repo
  -> clone
  -> profile deterministic evidence
  -> infer runnable shape
  -> produce candidate RunPlan(s)
  -> execute in Docker Compose sandbox
  -> adjudicate oracle(s)
  -> discover/smoke probe
  -> write trusted report + runbook + reproduce artifact
```

The system should report one of these outcomes honestly:

- `verified:<shape>`: a RunPlan was actually exercised and passed its oracle.
- `failed:<shape>`: a RunPlan was attempted, but verification failed.
- `deferred`: repo-pilot recognized the repo requires unsupported handling.
- `not_runnable:<shape>`: the repo is docs-only or otherwise not a runnable system.
- `no_candidate`: repo-pilot found no credible way to run or exercise it.
- `error`: infrastructure failure, dependency failure, or unexpected exception.

## Current State

The architectural spine is already in the right shape:

- `repo_pilot/run_shape.py` owns canonical `RunShape`, `RunPlan`, `RunComponent`, and `Oracle`.
- `repo_pilot/candidate_planning.py` produces canonical plans for common Node, Python, Go, and Make shapes.
- `repo_pilot/run_verifier.py` verifies RunPlans through one sandbox/oracle interface.
- `repo_pilot/compose.py` can compile RunPlan components and create portable reproduce compose artifacts.
- `repo_pilot/graph.py` uses `RunPlan` as the pipeline spine and projects v1 runbook artifacts at the boundary.
- `repo_pilot/eval.py` already has the start of a shape-aware coverage scoring model.

The main remaining gap is not another large refactor. It is measured coverage: real repositories, failure clusters, and focused vertical slices.

## Success Metrics

Use a pinned eval manifest as the source of truth.

Milestones:

- **M1:** 50 pinned repos, all cases runnable through eval harness without crashing the sweep.
- **M2:** >= 50% correct verdicts across the 50-case manifest.
- **M3:** >= 70% correct verdicts across the 50-case manifest.
- **M4:** >= 80% correct verdicts across the 50-case manifest.
- **M5:** Expand to 200 pinned repos and hold >= 75% correct verdicts.

Correctness means the produced verdict matches the expected canonical token, for example:

- `verified:service`
- `verified:cli`
- `verified:library`
- `verified:build`
- `verified:batch`
- `verified:multi_component_service`
- `not_runnable:docs`
- `deferred`
- `no_candidate`

## Cross-Cutting Constraints (ADR-0020)

The primary consumer of the artifact bundle is an AI coding agent that re-runs
it against a modified working tree; repo-pilot's boundary is bring-up, not
downstream testing (UI tests, functional suites). Every phase below must
preserve:

1. **Re-runnable against a modified tree.** `compose.generated.yaml` keeps
   working when the repo checkout has local edits (writable repo-code
   components, PRs #60/#61). Add regression tests when executor/compose build
   behavior changes.
2. **Reach and readiness are machine-readable.** Ports, base URLs, readiness
   oracles, and component roles land in structured `runbook.yaml` fields —
   never only as prose in `report.md`.
3. **Converge on a single-command entry.** Reproduce blocks trend toward one
   command that brings the system up, checks readiness, and exits meaningfully.

See `docs/adr/0020-agent-as-primary-consumer.md`.

## Non-Goals

Do not try to make repo-pilot run absolutely every repository in one pass.

Do not execute a target repository's own `docker-compose.yml` verbatim.

Do not make the LLM the source of truth. LLMs may propose plans; Docker sandbox and deterministic oracles adjudicate truth.

Do not add broad ecosystem support without eval cases that justify and validate it.

---

## Phase 0: Restore Local Verification

Before expanding capability, restore full local test execution. Current `.venv/bin/python -m pytest` is blocked by the local Xcode `python` lookup failure.

### Task 0.1: Rebuild the Development Environment

**Files:**
- Modify if needed: `docs/USAGE.md`
- Modify if needed: `pyproject.toml`

**Steps:**

1. Remove or replace the broken local virtualenv outside of a code commit.
2. Create a Python 3.11 or 3.12 virtualenv.
3. Install the project:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[dev]"
```

4. Run:

```bash
.venv/bin/python -m pytest -q
```

**Expected:** default non-integration tests collect and run.

**Commit:** only commit documentation or project metadata changes, not the virtualenv.

---

## Phase 1: Build the Coverage Feedback Loop

This is the highest-priority phase. The project needs a repeatable way to answer "how far are we from the goal?"

### Task 1.1: Add an Eval Manifest Format and First 50 Cases

**Files:**
- Create: `eval/manifest.50.json`
- Modify: `docs/eval-harness.md`
- Test: `tests/test_eval.py`

**Manifest shape:**

```json
[
  {
    "name": "express-basic",
    "repo_url": "https://github.com/example/express-basic",
    "commit": "PINNED_SHA",
    "expected": "verified:service",
    "notes": "Express app with npm start"
  }
]
```

**Initial distribution:**

- 10 Node services: Express, Next, Vite, plain HTTP server.
- 10 Python services: Flask, FastAPI, Django.
- 5 Go projects: CLI and HTTP service.
- 5 Python CLI/library projects.
- 5 Make build/batch projects.
- 10 compose-first projects.
- 5 docs-only or intentionally unrunnable repos.

**Acceptance:**

- Every case pins a commit.
- Every case has one expected canonical verdict.
- The manifest intentionally includes unsupported repos so deferred/no_candidate paths are exercised.

### Task 1.2: Add a CLI Command for Eval

**Files:**
- Modify: `repo_pilot/cli.py`
- Modify: `repo_pilot/eval.py`
- Test: `tests/test_eval.py`

**Behavior:**

Add:

```bash
repo-pilot eval eval/manifest.50.json --workdir artifacts/eval-runs
```

The command should:

1. Load cases with `load_manifest`.
2. Run each case through the real graph.
3. Continue after per-case exceptions.
4. Print markdown from `format_report`.
5. Exit non-zero when coverage is below a configurable threshold.

**Options:**

```text
--threshold FLOAT     default 0.5 initially
--no-llm             pass through to graph setup
--limit INT          run only first N cases for local debugging
--case NAME          run one named case
```

**Acceptance:**

- A single crashing repo records `error` and does not abort the sweep.
- The final report includes overall coverage, per-shape coverage, and failure clusters.

### Task 1.3: Persist Per-Case Eval Artifacts

**Files:**
- Modify: `repo_pilot/eval.py`
- Modify: `repo_pilot/artifacts.py`
- Test: `tests/test_eval.py`

**Behavior:**

Each eval case should write artifacts under:

```text
artifacts/eval-runs/<timestamp>/<case-name>/
```

Include:

- `report.md`
- `runbook.yaml` if produced
- `repo-profile.json`
- `evidence.jsonl`
- `compose.generated.yaml` if verified
- `final-state-summary.json`

**Acceptance:**

- Failure cluster entries point to the case artifact directory.
- Eval reports are useful enough to drive implementation without rerunning immediately.

---

## Phase 2: Compose-Native Controlled Import

This is the largest coverage unlock. Many real repos are compose-first, but repo-pilot must not execute target compose files verbatim.

### Task 2.1: Create Compose Importer Module

**Files:**
- Create: `repo_pilot/compose_import.py`
- Create: `tests/test_compose_import.py`
- Modify: `repo_pilot/candidate_planning.py`

**Purpose:**

Convert a safe subset of a target repo's compose file into canonical `RunPlan`.

Supported input files:

- `docker-compose.yml`
- `docker-compose.yaml`
- `compose.yml`
- `compose.yaml`

**Supported service fields in first slice:**

- `image`
- `build.context`
- `build.dockerfile`
- `command`
- `ports`
- `environment`
- `depends_on`
- `healthcheck`
- `working_dir`

**Rejected fields in first slice:**

- `privileged: true`
- `network_mode: host`
- `pid: host`
- `ipc: host`
- Docker socket mounts
- absolute host mounts
- external networks
- `extends`
- unknown build contexts outside repo

**Return model:**

```python
@dataclass(frozen=True)
class ComposeImportResult:
    plan: RunPlan | None
    deferred_reason: str | None
    warnings: list[str]
```

**Acceptance tests:**

- Simple app + postgres compose imports to `RunShape.MULTI_COMPONENT_SERVICE`.
- Unsafe Docker socket mount returns `deferred_reason == "unsafe-compose"`.
- Compose with only database services returns no plan.
- Imported plan passes `normalize_plan`.

### Task 2.2: Identify Repo-Code Services

**Files:**
- Modify: `repo_pilot/compose_import.py`
- Test: `tests/test_compose_import.py`

**Rules:**

A service is repo-code if:

- it has `build.context: .`, or
- it has `build.context` inside the repo, or
- it has a command that references app startup and a local build context.

A service is managed dependency if:

- it has `image` and no build context, and
- it looks like postgres, redis, mysql, mariadb, mongo, rabbitmq, elasticsearch, or similar.

**Oracle mapping:**

- Repo-code web service with published port -> `http` oracle.
- Repo-code service with no port -> `process-up` or `log-ready` if healthcheck/log signal exists.
- Managed DB/cache with healthcheck -> `native-cmd`.
- Managed DB/cache without healthcheck -> `process-up` for first slice.

**Acceptance:**

- Imported components preserve `depends_on`.
- Repo-code components get `workdir="/workspace/repo"` when not specified.
- Imported dependency components do not get repo build contexts.

### Task 2.3: Wire Compose Import into Planning

**Files:**
- Modify: `repo_pilot/candidate_planning.py`
- Modify: `repo_pilot/extractors.py`
- Test: `tests/test_candidate_planning.py`
- Test: `tests/test_graph_components.py`

**Behavior:**

When `compose_service` evidence is present:

1. Try controlled compose import.
2. If safe and runnable, return imported `RunPlan`.
3. If unsafe, return `deferred_reason="unsafe-compose"`.
4. If too complex, return `deferred_reason="needs-compose"`.

**Acceptance:**

- A safe compose-first fixture verifies through existing `verify_run_plan`.
- Unsafe compose is deferred, not executed.
- Report clearly says why deferred.

---

## Phase 3: Dockerfile-First Support

Many projects have a Dockerfile but no clear package script. This phase turns Dockerfile evidence into runnable plans.

### Task 3.1: Parse Dockerfile Runtime Signals

**Files:**
- Create: `repo_pilot/dockerfile_inspect.py`
- Create: `tests/test_dockerfile_inspect.py`
- Modify: `repo_pilot/profiler.py`

**Extract:**

- `FROM`
- `WORKDIR`
- `EXPOSE`
- `CMD`
- `ENTRYPOINT`
- package manager hints from `RUN`

**Result:**

```python
@dataclass(frozen=True)
class DockerfileRuntime:
    path: str
    base_image: str | None
    workdir: str | None
    exposed_ports: list[int]
    command: str | None
```

**Acceptance:**

- JSON-form and shell-form `CMD` parse correctly.
- Dockerfiles with no runtime command produce no service candidate by themselves.

### Task 3.2: Plan Dockerfile-First Service

**Files:**
- Modify: `repo_pilot/candidate_planning.py`
- Modify: `repo_pilot/compose.py` if build metadata needs to be represented
- Test: `tests/test_candidate_planning.py`
- Test: `tests/test_compose.py`

**Behavior:**

For a Dockerfile with `CMD`/`ENTRYPOINT` and `EXPOSE`:

- produce `RunShape.SERVICE`
- use `http` oracle on exposed port
- build from repo using Dockerfile rather than inline Dockerfile when reproducing

**Design constraint:**

Do not bypass sandbox hardening. The generated compose should still own security fields and verification.

**Acceptance:**

- Dockerfile-only HTTP fixture verifies.
- Reproduce report uses `docker compose -f compose.generated.yaml up --build`.

---

## Phase 4: Monorepo and Workspace Support

This phase targets modern JS/TS repos and multi-package layouts.

### Task 4.1: Profile Node Workspaces

**Files:**
- Modify: `repo_pilot/profiler.py`
- Test: `tests/test_profiler.py`

**Detect:**

- npm workspaces
- pnpm workspaces
- yarn workspaces
- `apps/*/package.json`
- `packages/*/package.json`
- turborepo `turbo.json`
- nx `nx.json`

**Profile shape:**

Add workspace-aware entrypoints:

```json
{
  "type": "script",
  "file": "apps/web/package.json",
  "workspace": "apps/web",
  "key": "dev",
  "command": "npm --workspace apps/web run dev"
}
```

**Acceptance:**

- A fixture with `apps/web/package.json` produces a service entrypoint.
- Root package manager is reused.

### Task 4.2: Plan Workspace Services

**Files:**
- Modify: `repo_pilot/candidate_planning.py`
- Test: `tests/test_candidate_planning.py`

**Behavior:**

- Use root repo as build context.
- Set workdir to `/workspace/repo`.
- Run workspace command from root.
- Prefer app workspaces over library packages when both exist.

**Acceptance:**

- Workspace service fixture verifies with fake executor.
- Evidence refs point to the workspace package file.

---

## Phase 5: Ecosystem Expansion

Only add ecosystems with eval cases. Suggested order is Java, Rust, then Ruby/PHP/.NET.

### Task 5.1: Java Maven/Gradle

**Files:**
- Modify: `repo_pilot/profiler.py`
- Modify: `repo_pilot/candidate_planning.py`
- Test: `tests/test_candidate_planning.py`
- Add eval cases: `eval/manifest.50.json`

**Detect:**

- `pom.xml`
- `build.gradle`
- `build.gradle.kts`
- `gradlew`
- Spring Boot dependencies/plugins

**Plan:**

- Library/build: `mvn test`, `./gradlew test`
- Service: `mvn spring-boot:run`, `./gradlew bootRun`
- Image: `eclipse-temurin:21`
- Default port: `8080`

**Acceptance:**

- Maven test fixture verifies as `verified:library`.
- Spring Boot fixture verifies as `verified:service`.

### Task 5.2: Rust Cargo

**Files:**
- Modify: `repo_pilot/profiler.py`
- Modify: `repo_pilot/candidate_planning.py`
- Test: `tests/test_candidate_planning.py`
- Add eval cases.

**Detect:**

- `Cargo.toml`
- binary targets
- tests

**Plan:**

- `cargo test` -> `verified:library`
- `cargo build` -> `verified:build`
- `cargo run -- ...` when a binary can be safely exercised
- Image: `rust:1-bookworm`

**Acceptance:**

- Rust library fixture verifies through `cargo test`.
- Rust CLI fixture verifies through a meaningful command if available.

---

## Phase 6: Stronger Trust in Reports

The report should explain why the result is trustworthy, not only state that it passed.

### Task 6.1: Add Evidence Section to Reports

**Files:**
- Modify: `repo_pilot/report.py`
- Modify: `repo_pilot/graph.py`
- Test: `tests/test_report.py`

**Behavior:**

Include an `## Evidence` section with selected evidence refs:

```markdown
## Evidence

- package.json scripts.start = "npm start"
- express dependency detected
- HTTP oracle passed: GET /health -> 404
```

**Acceptance:**

- Report includes evidence for deterministic plans.
- Agent-proposed plans include the agent evidence item with low confidence.

### Task 6.2: Add Structured Failure Reasons

**Files:**
- Modify: `repo_pilot/run_verifier.py`
- Modify: `repo_pilot/component_oracles.py`
- Modify: `repo_pilot/report.py`
- Test: `tests/test_run_verifier.py`
- Test: `tests/test_report.py`

**Failure categories:**

- `install_failed`
- `container_exited`
- `port_not_published`
- `http_unreachable`
- `dependency_unhealthy`
- `timeout`
- `unsafe_compose`
- `docker_unavailable`
- `unknown`

**Acceptance:**

- Failed report includes a concise reason before logs.
- Eval failure clusters can group by structured reason.

### Task 6.3: Improve Reproduce Instructions

**Files:**
- Modify: `repo_pilot/report.py`
- Test: `tests/test_report.py`

**For verified multi-component plans:**

```bash
git clone <url> repo
docker compose -f compose.generated.yaml up --build
docker compose -f compose.generated.yaml ps
docker compose -f compose.generated.yaml logs --tail=80
```

**For single-component plans:**

```bash
git clone <url> repo
cd repo
<verified command>
```

**Acceptance:**

- Report distinguishes artifact-directory commands from in-repo commands.
- Reproduce block never references a compose file that was not written.

---

## Phase 7: Documentation and Product Boundary

### Task 7.1: Update Usage Documentation

**Files:**
- Modify: `docs/USAGE.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify if needed: `docs/glossary.md`

**Fix stale claims:**

Current `docs/USAGE.md` still describes an older v1 scope. Update it to reflect:

- Node/Python/Go/Make deterministic paths.
- service/cli/library/build/batch/multi-component/docs shapes.
- compose-native status after Phase 2.
- repair loop status.
- weak-oracle vs strong-oracle boundary.
- eval workflow.

**Acceptance:**

- User docs match current code behavior.
- Unsupported cases are stated as explicit product boundaries.

### Task 7.2: Add Operator Playbook

**Files:**
- Create: `docs/operator-playbook.md`

**Include:**

- How to run one repo.
- How to inspect artifacts.
- How to rerun reproduce compose.
- How to run eval.
- How to read failure clusters.
- How to add a new ecosystem safely.

**Acceptance:**

- A new contributor can add one eval case and run it locally using the playbook.

---

## Implementation Order

Use this exact order unless eval data clearly suggests otherwise:

1. Phase 0: restore full tests.
2. Phase 1: eval manifest + CLI + artifacts.
3. Phase 7.1: update stale docs so users understand current boundaries.
4. Phase 2: compose-native controlled import.
5. Phase 3: Dockerfile-first support.
6. Phase 4: monorepo/workspace support.
7. Phase 5: Java/Rust ecosystem expansion.
8. Phase 6: report trust improvements.
9. Phase 7.2: operator playbook.

## Commit Strategy

Commit after every task or small vertical slice.

Suggested commit prefixes:

- `test: add eval cases for runtime coverage`
- `feat: add eval cli`
- `feat: import safe compose services as run plans`
- `feat: plan dockerfile-first services`
- `feat: detect node workspaces`
- `feat: add java runtime planning`
- `docs: update current runtime support`

## Definition of Done

A capability is done only when all are true:

1. It has at least one eval case.
2. It has unit tests for parsing/planning logic.
3. It has a vertical graph or verifier test.
4. It produces a clear verified/failed/deferred outcome.
5. A verified outcome includes reproduce instructions that match the artifact actually written.
6. Documentation names both supported and unsupported behavior.

## Risk Register

### Risk: Unsafe compose import

Mitigation: controlled import only; unsafe fields defer. Never execute target compose verbatim.

### Risk: LLM proposes plausible but wrong plans

Mitigation: `normalize_plan` rejects invalid plans; sandbox oracle adjudicates success; evidence confidence remains low for agent-only plans.

### Risk: Coverage grows but trust weakens

Mitigation: do not count static detection as success. Only verified oracle results produce `verified:*`.

### Risk: Eval becomes flaky

Mitigation: pin commits, record artifacts, isolate per-case working dirs, classify infrastructure errors separately from repo failures.

### Risk: Reproduce differs from verification

Mitigation: keep `reproduce_compose()` aligned with executor semantics; add regression tests whenever executor build behavior changes.

---

## Immediate Next Actions

Start with these three pull requests:

1. **PR 1:** Restore test environment docs and add `repo-pilot eval` skeleton using existing `eval.py`.
2. **PR 2:** Add `eval/manifest.50.json` with pinned cases and artifact persistence.
3. **PR 3:** Implement `compose_import.py` safe subset with tests and wire it into `candidate_planning.py`.

After those, run the 50-case eval and let the largest failure clusters choose the next slice.
