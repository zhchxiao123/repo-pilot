# Coverage eval harness (#44)

The harness measures how often repo-pilot produces a **correct verdict** across a
set of repos — the metric behind the goal: given 1000 repos, correctly output the
startup method for ≥900 (≥90% coverage). It's the feedback loop for improving the
plan agent and oracle library: run it, read the failure clusters, fix the dominant
one, re-run.

## Verdict categories

`repo_pilot/eval.py::verdict_of` reduces a graph final state to one of:

| verdict | meaning |
|---------|---------|
| `verified` | the run flow was sandbox-verified — a service came up, or a non-service was exercised to a clean result (ADR-0018) |
| `not-a-service` | correctly judged not a runnable system (docs-only, …) |
| `failed` | a candidate was tried but did not verify |
| `deferred` / `no-candidate` | nothing was run |

A case is **correct** when the actual verdict equals the case's `expected`.
Coverage = correct / total.

## Manifest

A JSON array of cases (`eval/manifest.example.json`):

```json
[
  {"name": "my-service", "repo_url": "https://github.com/org/repo", "expected": "verified", "commit": "abc123"},
  {"name": "just-docs",  "repo_url": "https://github.com/org/docs", "expected": "not-a-service"}
]
```

`repo_url` is anything `git clone` accepts (a URL or a local git path); `commit`
is optional.

## Running

Needs Docker (and an LLM key for the plan agent, or `--no-llm` for rules only):

```
python -m repo_pilot.eval eval/manifest.example.json --out eval/report.md
```

It prints a markdown report: coverage, a per-case OK/XX line, and **failure
clusters** grouped by `expected->actual` (dominant first) so you can see *how*
coverage is missed. A single case that errors is recorded as `error`, not fatal.
The process exits non-zero when coverage < 90%, so CI can gate on it.
