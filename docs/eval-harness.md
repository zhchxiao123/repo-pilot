# Coverage eval harness (#44)

The harness measures how often repo-pilot produces a **correct verdict** across a
set of repos — the metric behind the goal: given 1000 repos, correctly output the
startup method for ≥900 (≥90% coverage). It's the feedback loop for improving the
plan agent and oracle library: run it, read the failure clusters, fix the dominant
one, re-run.

## Verdict categories

`repo_pilot/eval.py::verdict_of` reduces a graph final state to a canonical
compound `kind:shape` verdict (or a bare kind when shape does not apply):

| verdict | meaning |
|---------|---------|
| `verified:<shape>` | sandbox-verified — a service came up, or a non-service (cli/library/build/batch) was exercised to a clean result (ADR-0018/0019) |
| `not_runnable:<shape>` | correctly judged not a runnable system (docs-only, …) |
| `failed` | a candidate was tried but did not verify |
| `deferred` / `no_candidate` | nothing was run |

A case is **correct** when its `expected` matches the actual verdict
*hierarchically*: a coarse `verified` subsumes any `verified:<shape>`, and legacy
tokens (`not-a-service`, `no-candidate`) alias to their canonical form — so older
manifests keep scoring without edits. Overall coverage = correct / total, and the
report also breaks coverage down **per shape** (service/cli/library/…), so a
regression in one shape is visible even when overall coverage looks fine.

## Manifest

A JSON array of cases (`eval/manifest.example.json`):

```json
[
  {"name": "my-service", "repo_url": "https://github.com/org/repo", "expected": "verified:service", "commit": "abc123"},
  {"name": "a-cli",      "repo_url": "https://github.com/org/cli",  "expected": "verified:cli"},
  {"name": "just-docs",  "repo_url": "https://github.com/org/docs", "expected": "not_runnable:docs"}
]
```

`repo_url` is anything `git clone` accepts (a URL or a local git path); `commit`
is optional. `expected` may be a coarse kind (`verified`) or a compound
`kind:shape` (`verified:cli`). A free-form `notes` field is allowed (and
ignored by the loader).

### The pinned 50-case manifest

`eval/manifest.50.json` is the source of truth for the coverage milestones
(M1–M4 in the expansion plan): 50 real repos, every case pinned to a commit,
every case with one expected canonical verdict. The distribution intentionally
includes compose-first repos (blocked until compose-native import lands) and
docs-only repos, so the `deferred`/`no_candidate`/`not_runnable` paths are
exercised — a case that can't be run *correctly* is still a case.
`tests/test_eval.py::test_manifest_50_pins_fifty_canonical_cases` guards the
invariants (50 unique names, pinned 40-hex commits, canonical verdicts).

## Running

Needs Docker (and an LLM key for the plan agent, or `--no-llm` for rules only):

```
repo-pilot eval eval/manifest.example.json --workdir artifacts/eval-runs
```

Options:

- `--workdir DIR` — per-case artifacts land under `DIR/<case-name>/` (default
  `artifacts/eval-runs`)
- `--threshold FLOAT` — exit non-zero when overall coverage falls below this
  fraction (default `0.5`), so CI can gate on it
- `--no-llm` — deterministic path only
- `--limit N` / `--case NAME` — run a subset for local debugging

It prints a markdown report: overall + per-shape coverage, a per-case OK/XX line,
and **failure clusters** grouped by `expected->actual` (dominant first) — the
shape rides along in the signature (e.g. `verified:service->failed`) so you can
see *which shape* is missing coverage and *how*. A single case that errors is recorded as `error`, not fatal.

## Per-case artifacts

Each sweep writes into a timestamped run directory, one sub-directory per case:

```
artifacts/eval-runs/<timestamp>/
  eval-report.md              # the sweep report printed to stdout
  <case-name>/
    report.md
    runbook.yaml              # if produced
    repo-profile.json
    evidence.jsonl
    compose.generated.yaml    # if a compose plan was compiled
    final-state-summary.json  # verdict, expected, correct, deferred_reason, log tail
```

`final-state-summary.json` is written even when the case crashes (with an
`error` field), and failure-cluster entries in the report point at each case's
artifact directory — so a sweep can drive the next fix without rerunning.

(`python -m repo_pilot.eval` remains as a legacy entry point with a fixed 90%
gate; prefer the `repo-pilot eval` command.)
