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
`kind:shape` (`verified:cli`).

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

(`python -m repo_pilot.eval` remains as a legacy entry point with a fixed 90%
gate; prefer the `repo-pilot eval` command.)
