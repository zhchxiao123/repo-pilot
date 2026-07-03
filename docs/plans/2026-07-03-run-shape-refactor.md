# Run Shape Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor repo-pilot so its primary job is to discover, express, verify, and report the most appropriate runnable shape of a repository: service, CLI, library, batch job, build/validation target, docs-only repo, or multi-component system.

**Architecture:** Introduce a first-class Run Shape domain model behind a small interface, then make planning, verification, reporting, and evaluation consume that model. The existing v1 Runbook remains the persisted compatibility artifact during the transition, but `components[] + oracle` becomes the canonical internal representation and legacy `runtime/steps/healthcheck/services` becomes a projection.

**Tech Stack:** Python 3.11+, dataclasses/typing, JSON Schema, pytest, LangGraph, Docker Compose, LangChain tool-calling seam.

---

## Product Definition

repo-pilot does not merely "analyze a project." It analyzes a project in order to find a reproducible way to run or exercise it.

The core product loop is:

```text
repo input
  -> clone
  -> profile evidence
  -> infer runnable shape
  -> produce candidate run plans
  -> execute in sandbox
  -> adjudicate shape-specific success oracle
  -> report verified runbook + evidence + reproduce instructions
```

Success is shape-specific:

- `service`: long-running process reaches HTTP/TCP/log/process oracle.
- `multi-component-service`: every component reaches its oracle.
- `cli`: a meaningful command runs to completion.
- `library`: tests/import/example run to completion.
- `batch`: job exits 0.
- `build`: build or validation exits 0.
- `docs`: honestly classified as not runnable.
- `unknown`: no confident runnable shape found.

## Current Code Diagnosis

The project already contains the right direction:

- `schemas/runbook.schema.json` has additive `components[]` and `verification.components[]`.
- `repo_pilot/oracles.py` defines shape-adjacent oracle types.
- `repo_pilot/component_oracles.py` adjudicates components against the sandbox.
- `repo_pilot/plan_agent.py` already prompts the model to classify non-service repos and propose component run plans.
- `repo_pilot/eval.py` reduces final state to verdict categories.

The main issue is architectural priority: `components[]` is currently an extension on top of the legacy single-service runbook, while the real product model should be "run shape + run plan + oracle." Legacy `runtime/steps/healthcheck` should be a compatibility projection, not the center.

## Target Module Map

Create these deep modules:

```text
repo_pilot/run_shape.py
  Owns RunShape, RunPlan, RunComponent, Oracle, VerificationResult dataclasses.
  Small interface: construct/validate/normalize/project.

repo_pilot/runbook_projection.py
  Converts canonical RunPlan <-> v1 Runbook dict.
  Keeps schema compatibility and persistence concerns out of planners/verifiers.

repo_pilot/shape_detection.py
  Deterministic shape detection from Profile + Evidence.
  Produces RunShapeHints, not final truth.

repo_pilot/candidate_planning.py
  Merges deterministic planners and LLM planner output into ranked canonical RunPlans.

repo_pilot/run_verifier.py
  Verifies a canonical RunPlan through SandboxExecutor.
  Hides single-app vs component verification from graph.py.

repo_pilot/outcome.py
  Terminal verdict model: verified, failed, deferred, not_runnable, no_candidate, error.

repo_pilot/report.py
  Renders from Outcome + RunPlan + evidence, not from ad hoc graph state.
```

`graph.py` should become mostly orchestration:

```python
clone -> profile -> plan_candidates -> verify_candidates -> discover -> test -> report
```

It should stop knowing how to enrich runbooks, compile components, inspect component results, or decide verdict semantics.

## Refactor Principles

1. **Canonical model first, compatibility second.**
   New code consumes `RunPlan`, not raw runbook dicts. Persisted YAML can remain v1 until schema v2 is ready.

2. **One verifier interface.**
   Callers should not branch on `if runbook.get("components")`. `run_verifier.verify(plan, executor, repo_dir)` owns that.

3. **Shape is not verdict.**
   `cli`, `library`, and `batch` are shapes. They can verify successfully. `docs` may be `not_runnable`.

4. **Evidence stays attached.**
   Shape detection, candidate generation, and repair must preserve evidence refs.

5. **LLM proposes, sandbox adjudicates.**
   Keep ADR-0004 intact. The model may classify and propose plans, but success comes only from oracle execution.

6. **TDD at new interfaces.**
   Existing tests on internal dict shapes should gradually move to the new module interfaces.

---

## Task 1: Add Canonical Run Shape Model

**Files:**
- Create: `repo_pilot/run_shape.py`
- Create: `tests/test_run_shape.py`
- Modify later only if needed: `repo_pilot/oracles.py`

**Step 1: Write failing tests**

Test the canonical model without Docker, LangGraph, or schema validation.

```python
from repo_pilot.run_shape import (
    Oracle,
    RunComponent,
    RunPlan,
    RunShape,
    normalize_plan,
)


def test_service_plan_requires_repo_code_component():
    plan = RunPlan(
        id="p1",
        shape=RunShape.SERVICE,
        components=[
            RunComponent(
                name="web",
                image="python:3.11",
                workdir="/workspace/repo",
                command="python app.py",
                ports=[8000],
                oracle=Oracle(type="http", port=8000, path="/health"),
            )
        ],
    )
    normalized = normalize_plan(plan)
    assert normalized.primary_component().name == "web"
    assert normalized.runnable is True


def test_docs_shape_is_not_runnable_without_components():
    plan = RunPlan(id="docs", shape=RunShape.DOCS, components=[])
    assert normalize_plan(plan).runnable is False
```

**Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_run_shape.py -q
```

Expected: import failure because `repo_pilot.run_shape` does not exist.

**Step 3: Implement minimal model**

Add:

- `RunShape` enum: `service`, `multi_component_service`, `cli`, `library`, `batch`, `build`, `docs`, `unknown`.
- `Oracle` dataclass with fields matching schema: `type`, `port`, `path`, `command`, `pattern`, `acceptable_status`.
- `RunComponent` dataclass: `name`, `image`, `role`, `workdir`, `command`, `env`, `ports`, `depends_on`, `oracle`.
- `RunPlan` dataclass: `id`, `shape`, `components`, `confidence`, `evidence_refs`, `repo`, `rationale`, `source`.
- `NormalizedRunPlan` wrapper with `runnable`, `primary_component()`, and `repo_code_components()`.
- `normalize_plan(plan)` that validates basic invariants and returns `NormalizedRunPlan`.

**Authoritative shape -> oracle mapping (single source of truth).**

Detection (Task 5), projection (Task 2), and planning (Task 6) must all consult
one table so they cannot disagree. Derive it from the oracle types already in
`oracles.py` and the classification guidance already in `plan_agent.py`:

```python
SHAPE_ORACLES: dict[RunShape, frozenset[str]] = {
    RunShape.SERVICE:                 frozenset({"http", "tcp-port", "log-ready", "process-up"}),
    RunShape.MULTI_COMPONENT_SERVICE: frozenset({"http", "tcp-port", "native-cmd", "log-ready", "process-up"}),
    RunShape.CLI:                     frozenset({"functional-smoke", "exit-zero", "stdio-handshake"}),
    RunShape.LIBRARY:                 frozenset({"tests-pass"}),
    RunShape.BATCH:                   frozenset({"exit-zero"}),
    RunShape.BUILD:                   frozenset({"build-succeeds", "exit-zero"}),
    RunShape.DOCS:                    frozenset(),   # not runnable
    RunShape.UNKNOWN:                 frozenset(),
}

# Inverse, for Task 2 runbook_to_plan shape inference. Ambiguous oracles that
# span shapes (exit-zero, http) are deliberately ABSENT: shape then comes from
# component role, never from a shared oracle.
ORACLE_PRIMARY_SHAPE: dict[str, RunShape] = {
    "tests-pass": RunShape.LIBRARY, "build-succeeds": RunShape.BUILD,
    "functional-smoke": RunShape.CLI, "stdio-handshake": RunShape.CLI,
    "native-cmd": RunShape.MULTI_COMPONENT_SERVICE, "log-ready": RunShape.SERVICE,
}
```

`normalize_plan` validates `oracle.type in SHAPE_ORACLES[plan.shape]` for every
component, requires `image` on every runnable component (it is the core execution
field — the verifier cannot start a component without it), and rejects a `docs`
plan that carries an oracle. This closes the
"Task 2 infers shape from oracle" vs "Task 5 detects shape first" disagreement:
ambiguous oracles never drive shape inference — role does.

Add a test asserting the invariant directly:

```python
import pytest
from repo_pilot.run_shape import RunShape, SHAPE_ORACLES, Oracle, RunComponent, RunPlan, normalize_plan


def test_docs_plan_rejects_oracle():
    plan = RunPlan(id="d", shape=RunShape.DOCS,
                   components=[RunComponent(name="c", image="python:3.11",
                              oracle=Oracle(type="http"))])
    with pytest.raises(ValueError):
        normalize_plan(plan)


def test_cli_plan_rejects_service_oracle():
    plan = RunPlan(id="c", shape=RunShape.CLI,
                   components=[RunComponent(name="c", image="python:3.11",
                              oracle=Oracle(type="http", port=80))])
    with pytest.raises(ValueError):
        normalize_plan(plan)
```

Keep this module pure. Do not import Docker, LangGraph, YAML, JSON Schema, or LangChain.

**Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_run_shape.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add repo_pilot/run_shape.py tests/test_run_shape.py
git commit -m "refactor: add canonical run shape model"
```

---

## Task 2: Add Runbook Projection Layer

**Files:**
- Create: `repo_pilot/runbook_projection.py`
- Create: `tests/test_runbook_projection.py`
- Use existing: `schemas/runbook.schema.json`

**Step 1: Write failing tests**

```python
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, RunShape
from repo_pilot.runbook_projection import runbook_to_plan, plan_to_runbook
from repo_pilot.schemas import validate_runbook


REPO = {"url": "https://x/y", "commit": "abc"}


def test_component_plan_projects_to_v1_runbook():
    plan = RunPlan(
        id="fullstack",
        shape=RunShape.MULTI_COMPONENT_SERVICE,
        repo=REPO,
        evidence_refs=["ev_agent1"],
        components=[
            RunComponent(
                name="db",
                image="postgres:16",
                oracle=Oracle(type="native-cmd", command="pg_isready"),
            ),
            RunComponent(
                name="backend",
                image="python:3.11",
                workdir="/workspace/repo",
                command="uvicorn app:app --port 8000",
                ports=[8000],
                depends_on=["db"],
                oracle=Oracle(type="http", port=8000, path="/health"),
            ),
        ],
    )
    rb = plan_to_runbook(plan, status="candidate")
    validate_runbook(rb)
    assert rb["components"][1]["name"] == "backend"
    assert rb["runtime"]["image"] == "python:3.11"


def test_legacy_single_service_runbook_imports_as_service_plan():
    rb = {
        "schema_version": "v1",
        "id": "node_npm_start",
        "status": "candidate",
        "repo": REPO,
        "runtime": {"image": "node:20-bookworm", "workdir": "/workspace/repo"},
        "steps": {"start": [{"command": "npm start", "expected_ports": [3000]}]},
        "healthcheck": {"strategy": "http", "url_candidates": ["/"]},
        "evidence_refs": ["ev_1"],
    }
    plan = runbook_to_plan(rb)
    assert plan.shape == RunShape.SERVICE
    assert plan.components[0].command == "npm start"


import pytest
from repo_pilot.run_shape import normalize_plan


@pytest.mark.parametrize("shape,oracle", [
    (RunShape.SERVICE, Oracle(type="http", port=8000, path="/health")),
    (RunShape.CLI, Oracle(type="functional-smoke")),
    (RunShape.LIBRARY, Oracle(type="tests-pass", command="pytest")),
    (RunShape.BUILD, Oracle(type="build-succeeds", command="make")),
    # BATCH uses the ambiguous exit-zero oracle, so shape can only be recovered
    # from persisted component `role` — this case guards that path specifically.
    (RunShape.BATCH, Oracle(type="exit-zero")),
])
def test_shape_survives_projection_round_trip(shape, oracle):
    # The invariant most likely to silently break: project to v1 and back must
    # preserve shape. Gated here so drift is caught the moment it appears.
    # NOTE: role is intentionally NOT set here — plan_to_runbook must derive and
    # persist it from shape, otherwise batch silently degrades to service.
    plan = RunPlan(id="x", shape=shape, repo=REPO,
                   components=[RunComponent(name="c", image="python:3.11",
                              workdir="/workspace/repo", command="run", oracle=oracle)])
    rb = plan_to_runbook(plan, status="candidate")
    assert rb["components"][0]["role"]                 # projection persisted a role
    assert runbook_to_plan(rb).shape == shape
```

**Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_runbook_projection.py -q
```

Expected: import failure.

**Step 3: Implement projection**

`plan_to_runbook(plan, status)`:

- Always writes current v1 required fields.
- For component plans, stores `components[]`.
- **Persists each component's `role` derived from the plan shape when not already
  set** (e.g. `RunShape.BATCH -> role "batch"`, `CLI -> "cli"`, `BUILD -> "build"`,
  `LIBRARY -> "library"`). v1 has no top-level `shape` field, so `role` is the only
  channel that lets `runbook_to_plan` recover a shape whose oracle is ambiguous
  (`exit-zero` for batch, `http` for service). Without this, batch silently
  degrades to service on the round trip.
- Synthesizes legacy `runtime`, `steps`, and `healthcheck` from the primary repo-code component.
- For CLI/library/batch/build, use `healthcheck: {"strategy": "http"}` only as schema compatibility until schema v2 removes this awkward requirement.

`runbook_to_plan(runbook)`:

- If `components[]` exists, import them directly.
- Else synthesize one component from legacy `runtime/steps/healthcheck`.
- Infer shape via the canonical `ORACLE_PRIMARY_SHAPE` table (Task 1), not ad hoc
  rules here. Order: **component `role` first** — `role` maps directly onto the
  shape enum (`cli -> CLI`, `batch -> BATCH`, `build -> BUILD`, `library -> LIBRARY`),
  which is what lets ambiguous-oracle shapes survive the round trip — then
  `ORACLE_PRIMARY_SHAPE[oracle.type]` for unambiguous oracles, otherwise
  `RunShape.SERVICE`. Ambiguous oracles (`exit-zero`, `http`) never decide shape
  on their own — this is what makes the round-trip test above hold.

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_runbook_projection.py tests/test_schemas.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add repo_pilot/runbook_projection.py tests/test_runbook_projection.py
git commit -m "refactor: project run plans to v1 runbooks"
```

---

## Task 3: Extract Verification Behind One Interface

**Files:**
- Create: `repo_pilot/run_verifier.py`
- Create: `tests/test_run_verifier.py`
- Modify: `repo_pilot/graph.py`
- Reuse: `repo_pilot/compose.py`
- Reuse: `repo_pilot/component_oracles.py`
- Reuse: `repo_pilot/healthcheck.py`

**Step 1: Write failing tests**

```python
from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, RunShape
from repo_pilot.run_verifier import verify_run_plan


def test_verify_service_plan_with_http_oracle(tmp_path):
    plan = RunPlan(
        id="svc",
        shape=RunShape.SERVICE,
        repo={"url": "u", "commit": "c"},
        components=[
            RunComponent(
                name="app",
                image="python:3.11",
                workdir="/workspace/repo",
                command="python app.py",
                ports=[8000],
                oracle=Oracle(type="http", port=8000, path="/health"),
            )
        ],
    )
    executor = FakeSandboxExecutor(
        component_ports={"app": {8000: 49152}},
        states={"app": ("running", None, None)},
        responses={"/health": 200},
    )
    result = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert result.verified is True
    assert result.component_results[0].name == "app"


def test_verify_cli_plan_by_exit_zero(tmp_path):
    plan = RunPlan(
        id="cli",
        shape=RunShape.CLI,
        repo={"url": "u", "commit": "c"},
        components=[
            RunComponent(
                name="cli",
                image="python:3.11",
                workdir="/workspace/repo",
                command="pip install -e . && tool sample",
                oracle=Oracle(type="functional-smoke"),
            )
        ],
    )
    executor = FakeSandboxExecutor(states={"cli": ("exited", None, 0)})
    assert verify_run_plan(plan, executor, repo_dir=str(tmp_path)).verified is True
```

**Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_run_verifier.py -q
```

Expected: import failure.

**Step 3: Implement verifier**

Add:

- `ComponentVerification` dataclass: `name`, `oracle`, `passed`, `detail`.
- `RunVerification` dataclass: `verified`, `logs_summary`, `ports`, `component_results`, `sandbox`, `compose`.
- `verify_run_plan(plan, executor, repo_dir, retries=0, poll_interval=1.0, sleep=time.sleep)`.

Implementation:

- Convert `RunPlan` to compose using a new helper or an internal projection to component dicts.
- Start sandbox once.
- Verify every component using `verify_component`.
- Stop sandbox on failure; keep sandbox alive on success for HTTP discovery/smoke.
- Return facts, not mutated runbook dicts.

**Step 4: Replace graph internals**

In `repo_pilot/graph.py`:

- Remove `_verify_components`.
- Convert selected runbook to `RunPlan` via `runbook_to_plan` while planners still return dicts.
- Call `verify_run_plan`.
- Convert verified/failed plan back to runbook with `plan_to_runbook`.
- Populate `verification` from `RunVerification`.

Keep behavior unchanged at the artifact boundary.

**Step 5: Run focused tests**

```bash
.venv/bin/python -m pytest \
  tests/test_run_verifier.py \
  tests/test_graph_components.py \
  tests/test_graph.py \
  tests/test_component_oracles.py \
  -q
```

Expected: pass.

**Step 6: Commit**

```bash
git add repo_pilot/run_verifier.py repo_pilot/graph.py tests/test_run_verifier.py
git commit -m "refactor: verify run plans through one interface"
```

---

## Task 4: Introduce Outcome Model

**Files:**
- Create: `repo_pilot/outcome.py`
- Create: `tests/test_outcome.py`
- Modify: `repo_pilot/eval.py`
- Modify: `repo_pilot/report.py`
- Modify: `repo_pilot/graph.py`

**Step 1: Write failing tests**

```python
from repo_pilot.outcome import Outcome, OutcomeKind, outcome_from_state


def test_verified_cli_is_verified_not_not_a_service():
    state = {
        "verified": True,
        "classification": "cli",
        "runbook": {"id": "cli"},
    }
    assert outcome_from_state(state).kind == OutcomeKind.VERIFIED


def test_docs_without_candidate_is_not_runnable():
    state = {
        "classification": "docs",
        "deferred_reason": "not-a-service:docs",
    }
    out = outcome_from_state(state)
    assert out.kind == OutcomeKind.NOT_RUNNABLE
    assert out.shape == "docs"
```

**Step 2: Implement model**

Add:

- `OutcomeKind`: `verified`, `failed`, `deferred`, `not_runnable`, `no_candidate`, `error`.
- `Outcome`: `kind`, `shape`, `summary`, `detail`, `verified`, `runnable`.
- **`outcome_from_verification(plan, verification)` — the canonical derivation, built first.**
- `outcome_from_state(state)` — a thin compat adapter that reconstructs a `RunPlan`
  (`runbook_to_plan`) and a `RunVerification` from legacy state, then *delegates* to
  `outcome_from_verification`.

Build the canonical function first and make the state path the adapter, not the
reverse. This keeps all dict-sniffing in one small adapter that gets deleted once
graph.py passes canonical objects (Task 11), instead of the state-based helper
becoming a foundation that never goes away.

```python
def outcome_from_verification(plan: NormalizedRunPlan, v: RunVerification | None) -> Outcome:
    if v is None:
        kind = OutcomeKind.NOT_RUNNABLE if not plan.runnable else OutcomeKind.NO_CANDIDATE
        return Outcome(kind, shape=plan.shape.value, verified=False, runnable=plan.runnable)
    kind = OutcomeKind.VERIFIED if v.verified else OutcomeKind.FAILED
    return Outcome(kind, shape=plan.shape.value, verified=v.verified, runnable=True)


def outcome_from_state(state: dict) -> Outcome:  # compat adapter — delegates
    plan = runbook_to_plan(state["runbook"]) if state.get("runbook") else _docs_plan(state)
    return outcome_from_verification(normalize_plan(plan), _verification_from_state(state))
```

**Step 3: Update eval**

In `repo_pilot/eval.py`, replace ad hoc `verdict_of` logic with `Outcome`.

**Eval vocabulary — one decision, shared with Task 10 (resolves the earlier
Task 4 / Task 10 conflict).** The canonical verdict is compound `kind:shape`
(`verified:cli`, `not_runnable:docs`). Matching is *hierarchical* and legacy
values *alias in*, so existing manifests keep passing with zero edits — no flag
day. Do not also emit the flat `not-a-service`; alias it instead.

```python
_ALIASES = {"not-a-service": "not_runnable", "no-candidate": "no_candidate"}  # old -> new

def verdict_of(final: dict) -> str:
    if final.get("verified"):
        return f"verified:{final.get('classification') or 'service'}"
    reason = final.get("deferred_reason")
    if isinstance(reason, str) and reason.startswith("not-a-service"):
        shape = reason.split(":", 1)[1] if ":" in reason else "docs"
        return f"not_runnable:{shape}"
    ...

def matches(expected: str, actual: str) -> bool:
    expected = _ALIASES.get(expected, expected)   # migrate legacy manifest values
    if expected == actual:
        return True
    return actual.startswith(expected + ":")       # coarse "verified" ⊇ "verified:cli"
```

`EvalResult.correct` uses `matches(...)` instead of `==`. Task 10 builds the
per-shape coverage report on top of this same compound vocabulary.

**Step 4: Update report**

`render_report` should display:

- Shape/classification.
- Outcome.
- If verified: what was exercised and how.
- If not runnable: why no run was attempted.
- If failed: which oracle failed.

**Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/test_outcome.py tests/test_eval.py tests/test_report.py -q
```

Expected: pass.

**Step 6: Commit**

```bash
git add repo_pilot/outcome.py repo_pilot/eval.py repo_pilot/report.py repo_pilot/graph.py tests/test_outcome.py
git commit -m "refactor: make terminal outcomes explicit"
```

---

## Task 5: Move Deterministic Shape Detection Out Of Planner

**Files:**
- Create: `repo_pilot/shape_detection.py`
- Create: `tests/test_shape_detection.py`
- Modify: `repo_pilot/profiler.py` only if additional profile fields are needed
- Modify: `repo_pilot/planner.py`

**Step 1: Write failing tests**

```python
from repo_pilot.shape_detection import detect_shapes


def test_detects_node_service_from_start_script():
    profile = {
        "languages": ["javascript"],
        "frameworks": ["express"],
        "package_managers": ["npm"],
        "entrypoints": [{"type": "script", "key": "start", "command": "node index.js"}],
    }
    hints = detect_shapes(profile, [])
    assert hints.primary.shape == "service"


def test_detects_library_from_package_without_start_but_with_tests():
    profile = {
        "languages": ["javascript"],
        "package_managers": ["npm"],
        "entrypoints": [{"type": "script", "key": "test", "command": "npm test"}],
    }
    hints = detect_shapes(profile, [])
    assert hints.primary.shape == "library"
```

**Step 2: Extend profiler carefully**

Current `profiler.py` only captures `dev` and `start` scripts. Extend it to capture:

- `test`
- `build`
- common CLI signals from `bin` in `package.json`

Do not yet add Python/Go/Rust detection in this task unless tests demand it.

**Step 3: Implement `shape_detection.py`**

Return ranked `ShapeHint` objects:

- `shape`
- `confidence`
- `evidence_refs`
- `reason`
- `commands`

Rules:

- start/dev + web framework -> service
- package `bin` -> cli
- test command without service start -> library
- build command without service start -> build
- README-only with no runnable evidence -> docs
- compose file -> multi_component_service or deferred compose import

**Step 4: Modify planner**

Keep `plan(profile, evidence)` return type initially, but internally call `detect_shapes`.

**Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/test_shape_detection.py tests/test_profiler.py tests/test_planner.py -q
```

Expected: pass.

**Step 6: Commit**

```bash
git add repo_pilot/shape_detection.py repo_pilot/profiler.py repo_pilot/planner.py tests/test_shape_detection.py
git commit -m "refactor: detect runnable shapes explicitly"
```

---

## Task 6: Make Candidate Planning Return Canonical RunPlans

**Files:**
- Create: `repo_pilot/candidate_planning.py`
- Create: `tests/test_candidate_planning.py`
- Modify: `repo_pilot/planner.py`
- Modify: `repo_pilot/plan_agent.py`
- Modify: `repo_pilot/graph.py`

**Step 1: Write failing tests**

```python
from repo_pilot.candidate_planning import plan_candidates
from repo_pilot.run_shape import RunShape


def test_node_start_script_becomes_service_run_plan():
    profile = {
        "repo": {"url": "u", "commit": "c"},
        "languages": ["javascript"],
        "frameworks": ["express"],
        "package_managers": ["npm"],
        "entrypoints": [{"type": "script", "key": "start", "command": "node index.js", "evidence_refs": ["ev_1"]}],
        "evidence_refs": {"package_manager:npm": ["ev_2"]},
    }
    result = plan_candidates(profile, evidence=[])
    assert result.candidates[0].shape == RunShape.SERVICE
    assert result.candidates[0].components[0].command == "npm start"
```

**Step 2: Implement new planner module**

`plan_candidates(profile, evidence, agent_result=None)` returns:

- `PlanningResult(candidates: list[RunPlan], deferred_reason: str | None, classification: str | None)`

Move deterministic Node candidate generation from `planner.py` into this module as canonical `RunPlan`.

**Step 3: Adapt old `planner.py`**

For compatibility, make `planner.plan()` call `plan_candidates()` and project `RunPlan` to v1 runbook dicts via `plan_to_runbook`.

This preserves existing tests while enabling the graph to move to canonical plans.

**Step 4: Adapt plan agent**

Change `_to_runbook` to `_to_run_plan`.

During transition:

- `explore_and_plan` returns canonical `RunPlan` candidates.
- If callers still expect dicts, project at the edge only.

**Step 5: Adapt graph**

Prefer canonical candidates:

- deterministic planning: `plan_candidates`
- LLM fallback: `explore_and_plan`
- artifact write: `plan_to_runbook`

**Step 6: Run tests**

```bash
.venv/bin/python -m pytest \
  tests/test_candidate_planning.py \
  tests/test_planner.py \
  tests/test_plan_agent.py \
  tests/test_graph.py \
  tests/test_graph_components.py \
  -q
```

Expected: pass.

**Step 7: Commit**

```bash
git add repo_pilot/candidate_planning.py repo_pilot/planner.py repo_pilot/plan_agent.py repo_pilot/graph.py tests/test_candidate_planning.py
git commit -m "refactor: plan canonical run shapes"
```

---

## Task 6.5: Prove One Non-Service Vertical Slice End To End

**Why:** Tasks 1-6 are all plumbing — no *new* user-visible capability lands until
Task 9 broadens profiling. That back-loads all product value and risk. This task
proves the entire canonical pipeline (detect -> plan -> verify -> outcome -> report)
on exactly one non-service repo at the project midpoint, converting the plumbing
into a demoable capability and de-risking Task 9 (which then only *broadens* proven
machinery rather than switching it on for the first time).

Scope is deliberately one fixture. Do not generalize here.

**Files:**
- Add fixture: `tests/fixtures/repos/python-cli/pyproject.toml` (with `[project.scripts]`)
- Modify: `repo_pilot/profiler.py` (read `[project.scripts]` into the profile — I/O lives here)
- Modify: `repo_pilot/shape_detection.py` (consume the profile field — no file access)
- Create: `tests/test_vertical_cli.py`

**Step 1: Add the fixture**

A minimal installable CLI: `pyproject.toml` declaring `[project.scripts] sample = "pkg:main"`
plus a trivial `pkg/__init__.py` whose `main()` prints and exits 0.

**Step 2: Failing end-to-end test (no Docker)**

```python
from repo_pilot.shape_detection import detect_shapes
from repo_pilot.candidate_planning import plan_candidates
from repo_pilot.run_verifier import verify_run_plan
from repo_pilot.outcome import outcome_from_verification, OutcomeKind
from repo_pilot.run_shape import RunShape, normalize_plan
from repo_pilot.executor import FakeSandboxExecutor


def test_python_cli_verifies_by_being_exercised(tmp_path):
    profile = {
        "repo": {"url": "u", "commit": "c"},
        "languages": ["python"],
        "package_managers": ["pip"],
        "entrypoints": [{"type": "script", "key": "sample", "command": "sample"}],
    }
    assert detect_shapes(profile, []).primary.shape == "cli"
    plan = plan_candidates(profile, evidence=[]).candidates[0]
    assert plan.shape == RunShape.CLI
    executor = FakeSandboxExecutor(states={plan.components[0].name: ("exited", None, 0)})
    v = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert v.verified is True
    assert outcome_from_verification(normalize_plan(plan), v).kind == OutcomeKind.VERIFIED
```

**Step 3: Implement the minimum to pass**

Split by responsibility so no I/O leaks into the rule module:

- `profiler.py` does the `tomllib` read of `pyproject.toml` and adds each
  `[project.scripts]` entry to the profile as a `script` entrypoint (this is the
  only place that touches the filesystem).
- `shape_detection.py` stays pure: it consumes that profile field and turns a
  script entrypoint into a `cli` hint with a `functional-smoke` oracle.

No Go/Make/FastAPI here — that is Task 9.

**Step 4: Run**

```bash
.venv/bin/python -m pytest tests/test_vertical_cli.py -q
```

Expected: pass — a non-service repo verifies through the full canonical pipeline.

**Step 5: Commit**

```bash
git add tests/fixtures/repos/python-cli repo_pilot/profiler.py repo_pilot/shape_detection.py tests/test_vertical_cli.py
git commit -m "test: prove python-cli verifies end to end through canonical pipeline"
```

---

## Task 7: Update Schema Toward v2 Without Breaking v1

**Files:**
- Create: `schemas/run-plan.schema.json` (new v2 — do **not** modify `runbook.schema.json`)
- Modify: `repo_pilot/schemas.py` (add `validate_run_plan`; leave `validate_runbook` untouched)
- Create: `tests/test_run_plan_schema.py`
- Modify: docs/ADR if needed

> Keep the migration surface small: v1 `runbook.schema.json` stays byte-for-byte
> unchanged. Adding v2 as a *separate* schema means v1 artifact validation cannot
> regress. Only touch v1 if a concrete compatibility field is genuinely required —
> and there is none in this plan.

**Step 1: Add v2 schema tests**

```python
from repo_pilot.schemas import validate_run_plan


def test_run_plan_schema_accepts_cli_shape():
    validate_run_plan({
        "schema_version": "v2",
        "id": "cli",
        "shape": "cli",
        "status": "candidate",
        "repo": {"url": "u", "commit": "c"},
        "components": [{
            "name": "cli",
            "role": "cli",
            "image": "python:3.11",
            "workdir": "/workspace/repo",
            "command": "tool sample",
            "oracle": {"type": "functional-smoke"}
        }]
    })
```

**Step 2: Create v2 schema**

`run-plan.schema.json` should make these first-class:

- `shape`
- `components`
- `oracle`
- `outcome`

It should not require `runtime`, `steps`, or `healthcheck`.

**Step 3: Add validator**

Add `validate_run_plan(document)` to `schemas.py`.

**Step 4: Keep v1 runbook validation**

Do not break existing artifact validation. v1 remains the default persisted `runbook.yaml` until the CLI has a migration story.

**Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/test_run_plan_schema.py tests/test_schemas.py -q
```

Expected: pass.

**Step 6: Commit**

```bash
git add schemas/run-plan.schema.json repo_pilot/schemas.py tests/test_run_plan_schema.py
git commit -m "feat: add canonical run plan schema"
```

---

## Task 8: Improve Report Around "How It Ran"

**Files:**
- Modify: `repo_pilot/report.py`
- Modify: `tests/test_report.py`
- Modify: `docs/USAGE.md`

**Step 1: Add report tests**

Add tests for:

- verified service
- verified CLI
- verified library/tests-pass
- docs not runnable
- failed multi-component with one failed oracle

Expected report sections:

```markdown
## Outcome
- Verdict: verified
- Shape: cli
- Exercised by: functional-smoke

## Run Plan
- cli: python:3.11
  - command: ...
  - oracle: functional-smoke

## Reproduce
...
```

**Step 2: Implement rendering**

Prefer rendering from `Outcome` and canonical `RunPlan`; keep old `runbook` parameter as compatibility input by converting with `runbook_to_plan`.

**Reproduce block must come from the RunPlan, not legacy `steps`.** Today
`_reproduce` in `graph.py` drives off `iter_step_commands(runbook)`. For CLI/
library/batch plans the legacy `steps` are *synthesized placeholders* (Task 2),
so that path yields wrong reproduce commands. Add `RunPlan.reproduce_commands()`
and render from it; keep `iter_step_commands` only for reading pre-existing v1
artifacts, never for authored plans.

**Reproduce granularity is shape-dependent — do not just concatenate every
component's `command`.** Dependency components (e.g. a `postgres` service) have
no `command` at all; listing raw commands would produce a broken, incomplete
recipe. Split it:

- **single-component shapes (`cli`, `library`, `build`, `batch`, single `service`):**
  reproduce from the primary component's `command` (plus its `image`/`workdir`).
- **`multi_component_service`:** reproduce is the *generated compose* — emit
  `docker compose up` against the plan's compiled compose (or a `repo-pilot
  reproduce`-style command), not a per-component command list. A component
  without a `command` (dependency image) is expected and must not break rendering.

Tests: a verified CLI's reproduce block equals its component command; a verified
multi-component plan's reproduce block is a `compose up`-style recipe and does not
emit an empty command for the dependency component.

**Step 3: Update docs**

Explain that repo-pilot may verify non-web repos by exercising them rather than starting a server.

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_report.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add repo_pilot/report.py tests/test_report.py docs/USAGE.md
git commit -m "docs: report runnable shape outcomes"
```

---

## Task 9: Expand Deterministic Profiling Beyond Node Skeleton

**Files:**
- Modify: `repo_pilot/profiler.py`
- Modify: `repo_pilot/extractors.py`
- Modify: `tests/test_profiler.py`
- Add fixtures under `tests/fixtures/repos/`

**Step 1: Add fixtures**

Create minimal fixtures:

- `python-cli/pyproject.toml`
- `python-lib/pyproject.toml`
- `python-fastapi/requirements.txt + app.py`
- `go-cli/go.mod + main.go`
- `make-build/Makefile`

**Step 2: Add failing profiler tests**

Assert detection of:

- Python package manager/install hints
- `console_scripts` or `[project.scripts]`
- FastAPI/Flask service hints
- Go main package as CLI or service depending on imports/listen signals
- Makefile build/test targets

**Step 3: Implement only cheap deterministic extraction**

Do not overbuild parsers. Use structured parsers where available:

- `tomllib` for `pyproject.toml`
- JSON for package manifests
- simple Makefile target regex

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_profiler.py tests/test_extractors.py tests/test_shape_detection.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add repo_pilot/profiler.py repo_pilot/extractors.py tests/test_profiler.py tests/fixtures/repos
git commit -m "feat: broaden deterministic runnable shape profiling"
```

---

## Task 10: Evaluate Against Shape Coverage

**Files:**
- Modify: `repo_pilot/eval.py`
- Modify: `eval/manifest.example.json`
- Modify: `docs/eval-harness.md`
- Create or modify: `tests/test_eval.py`

**Step 1: Update eval vocabulary**

Use the compound `kind:shape` vocabulary and hierarchical `matches()` already
defined in Task 4 (Step 3) — this task does not introduce a second, conflicting
scheme. Canonical expected values:

- `verified:service`
- `verified:cli`
- `verified:library`
- `verified:build`
- `not_runnable:docs`
- `failed`
- `deferred`
- `no_candidate`
- `error`

Backward compatibility is already handled by `_ALIASES` + prefix matching from
Task 4 (legacy `not-a-service` / `no-candidate` / coarse `verified` all still
match). Keep those aliases for one release, then drop them.

**Step 2: Add tests**

Test that:

- verified CLI is counted separately from verified service.
- docs-only no-candidate is not scored as failed if classified as not runnable.
- failure clusters include shape.

**Step 3: Implement format**

Coverage report should show:

```markdown
- Overall coverage: 91.0%
- Service coverage: 88.0%
- CLI coverage: 94.0%
- Library coverage: 90.0%

## Failure clusters
- expected verified:service -> failed:http
```

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_eval.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add repo_pilot/eval.py eval/manifest.example.json docs/eval-harness.md tests/test_eval.py
git commit -m "feat: evaluate runnable shape coverage"
```

---

## Task 11: Simplify graph.py After Migration

**Files:**
- Modify: `repo_pilot/graph.py`
- Modify: graph-related tests

**Step 1: Identify code to remove**

Remove or shrink these responsibilities from `graph.py`:

- `_verify_components`
- raw runbook enrichment details
- component verification result assembly
- terminal outcome decision logic
- direct compose compilation imports if no longer needed

`graph.py` should call:

- `plan_candidates`
- `verify_run_plan`
- `plan_to_runbook`
- `outcome_from_verification`
- `render_report`

**Step 2: Preserve behavior tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_graph.py tests/test_graph_components.py tests/test_cli.py -q
```

Expected: pass.

**Step 3: Delete obsolete tests**

If old tests assert implementation details of `_verify_components`, replace them with verifier interface tests.

**Step 4: Commit**

```bash
git add repo_pilot/graph.py tests/test_graph.py tests/test_graph_components.py tests/test_cli.py
git commit -m "refactor: make graph orchestration-only"
```

---

## Task 12: Repair Loop Works On RunPlan

**Files:**
- Modify: `repo_pilot/repair.py`
- Modify: `tests/test_repair.py`
- Possibly create: `repo_pilot/repair_prompts.py`

**Step 1: Add tests**

Cases:

- HTTP service failed because wrong port -> patch component oracle/ports.
- CLI failed because command missing install -> patch command.
- Dependency failed native-cmd -> patch env or healthcheck when rule-known.

**Step 2: Refactor repair input/output**

Current repair edits raw runbook dicts. Change to:

```python
propose_repair(plan: RunPlan, failure: RunVerification, model_client=None) -> RepairProposal | None
```

Projection to v1 happens only at artifact write.

**Step 3: Run tests**

```bash
.venv/bin/python -m pytest tests/test_repair.py tests/test_graph.py -q
```

Expected: pass.

**Step 4: Commit**

```bash
git add repo_pilot/repair.py tests/test_repair.py
git commit -m "refactor: repair canonical run plans"
```

---

## Task 13: Documentation And ADR Update

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/USAGE.md`
- Modify or add: `docs/adr/0019-run-shape-as-primary-model.md`
- Modify: `docs/glossary.md`

**Step 1: Add ADR**

ADR title:

```text
0019 - Run Shape is the primary model
```

Decision:

- The primary internal model is `RunPlan(shape, components, oracles)`.
- v1 Runbook remains persisted compatibility artifact.
- Single-service HTTP is one shape, not the whole product.

**Step 2: Update architecture**

Replace "Runbook spine" language with:

```text
Run Plan is the internal source of truth.
Runbook is the persisted reproducibility artifact.
```

If you want to be conservative, say:

```text
During v1 compatibility, Runbook remains persisted source of truth, but the internal pipeline operates on canonical Run Plans.
```

**Step 3: Update README examples**

Add examples:

- Express service verified.
- CLI exercised.
- Library tests pass.
- Docs-only not runnable.

**Step 4: Run doc-adjacent tests**

```bash
.venv/bin/python -m pytest tests/test_report.py tests/test_eval.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add README.md docs/ARCHITECTURE.md docs/USAGE.md docs/glossary.md docs/adr/0019-run-shape-as-primary-model.md
git commit -m "docs: define run shape as primary model"
```

---

## Task 14: Full Verification

**Files:**
- No code changes unless failures reveal bugs.

**Step 1: Run unit suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all non-integration tests pass.

**Step 2: Run type check**

```bash
.venv/bin/python -m mypy repo_pilot --ignore-missing-imports
```

Expected: no new errors.

**Step 3: Run focused CLI smoke on fixture**

```bash
repo-pilot run tests/fixtures/repos/express-min --artifacts-root ./artifacts
```

Expected:

- `Verified: True`
- report includes `Shape: service`
- runbook validates.

**Step 4: Optional Docker integration**

```bash
REPO_PILOT_COMPOSE_CMD="docker compose" \
  .venv/bin/python -m pytest -m integration -o addopts=""
```

Expected: integration suite passes if Docker is available.

**Step 5: Commit final cleanup**

```bash
git status --short
git add .
git commit -m "refactor: complete run shape pipeline"
```

---

## Migration Order Summary

1. Add canonical model.
2. Add projection to existing v1 runbook.
3. Extract verifier interface.
4. Add explicit outcome model.
5. Move deterministic shape detection out of planner.
6. Make planners return canonical RunPlans.
6.5. Prove one non-service (python-cli) vertical slice end to end.
7. Add v2 run-plan schema.
8. Improve reports.
9. Expand profiler.
10. Update eval harness.
11. Simplify graph.
12. Move repair loop to canonical plans.
13. Update docs/ADR.
14. Full verification.

## Risks

- Before Task 1, **gate** on the venv rather than unconditionally recreating it
  (the earlier "venv is broken" note is stale — verify, don't assume, so a working
  `.venv` is not blown away):

```bash
.venv/bin/python -c "import repo_pilot, langgraph, yaml, jsonschema" \
  && echo "venv OK — proceed" \
  || { rm -rf .venv && python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'; }
```

- Schema migration can sprawl. Keep v1 runbook compatibility until the pipeline is stable on canonical `RunPlan`.
- LLM prompt changes can destabilize tests. Keep fake tool-calling tests strict and deterministic.
- `graph.py` should be simplified only after the new modules are covered; do not combine extraction and deletion in one large commit.

## Definition Of Done

- A repository's verified result is described as a shape-specific outcome, not just an HTTP healthcheck.
- CLI/library/build/batch repos can verify successfully by being exercised.
- Multi-component repos verify when all components reach their oracles.
- `graph.py` is orchestration-only.
- Reports explain what was run, how it was verified, and how to reproduce it.
- Eval coverage can measure shape-specific success.
- Existing v1 runbook artifacts remain readable and valid.
