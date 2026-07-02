# ADR-0016 — Plan agent: LLM explores the repo itself and decides how to run it

Status: accepted
Date: 2026-07-02

## Context

Goal: correctly output the startup method for ≥90% of arbitrary repos. Deterministic
rules only recognize a few stacks; the previous LLM seams received a *static* dossier
and made a one-shot guess. Many repos don't document how to run — the instructions
are implicit in files (Dockerfile CMD, Procfile, Makefile, entry-point source), and
a fixed dossier can't anticipate which files matter. Some signals (e.g.
`Dockerfile.test`) are actively misleading. And some repos aren't runnable services
at all (libraries, docs, CLIs) — which must be reported honestly, not as a failure.

## Decision

Split responsibility by *who decides what*:

- **Explore (deterministic seed):** gather a light orientation dossier (file tree +
  a few key files) — `explore_tools.seed_context`. No decision.
- **Plan agent (LLM, `plan_agent.explore_and_plan`):** a bounded tool-calling loop.
  The agent uses **read-only, repo-confined tools** (`explore_tools.RepoTools`:
  `list_dir`/`read_file`/`search`/`find`) to read the repo itself, judges intent
  (ignoring test/CI artifacts), and calls `submit_plan` with a **classification**
  (service | cli | library | docs | monorepo | unknown) and ranked candidate
  Runbooks. Runs when deterministic rules find no candidate.
- **Verdict (sandbox):** unchanged — candidates are built and healthchecked; the
  sandbox decides truth. Non-service classifications are reported honestly
  ("not a runnable service — a docs repo"), not as failure.

Supersedes the one-shot `llm_planner`/`llm_profiler` (removed). Deterministic rules
remain as a fast path for recognized stacks and as seed evidence.

## Consequences

- The system reads code like a human to run undocumented/unrecognized stacks, and
  distinguishes "couldn't figure it out" from "nothing to run" (fixes e.g.
  `mattpocock/skills` reporting a false failure).
- LangGraph is now genuinely agentic (tool-calling in the plan node, cyclic repair).
- Tools are read-only + confined to the clone (untrusted repo can't read host
  files); execution stays exclusively in the sandbox.
- The agent needs a reachable LLM: without an API key the plan agent is unavailable
  and the CLI warns; rule-recognized stacks still run (or `--rules-only`).
- Follow-ups toward 90%: multi-candidate fallthrough, environment provisioning
  (services/runtime/env), and a coverage eval harness to measure and iterate.
