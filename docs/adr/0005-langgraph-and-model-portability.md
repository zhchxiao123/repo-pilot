# ADR-0005 — LangGraph orchestration + provider-agnostic model client

Status: accepted
Date: 2026-07-01

## Context

§16.1 waffles between "LangGraph / 自研 Agent Runner." Per ADR-0004 the system is
agent-first in orchestration. Two requirements drove the choice: high-autonomy
orchestration that will grow (the §9 repair agent doing multi-step
probe→propose→recheck; Phase 5–6 autonomy), and **model portability** — not being
locked to one provider.

A key distinction surfaced while grilling: orchestration framework and model
portability are *separate layers*. LangGraph provides orchestration; portability
comes from a provider-agnostic model client, not from the graph runtime. Using the
raw `anthropic` SDK directly would couple us to Claude's API shape regardless of
LangGraph.

## Decision

- **Orchestration: LangGraph.** Agent graphs drive the Tier-B/agent-first work
  (planning, novel-failure repair, strong-oracle test synthesis, NL extraction).
- **Model access: a provider-agnostic chat-model client** (LangChain chat model /
  LiteLLM-style adapter). No direct provider SDK calls from agent code.
- **Default model: Claude Opus 4.8 (`claude-opus-4-8`)**, best available in this
  ecosystem, selected *through* the abstraction so it is swappable by config.
  Cheaper seams (NL extraction) may default to Haiku 4.5 (`claude-haiku-4-5`).
- **Structured output** on every seam: schema-constrained, validated at the tool
  boundary, retry-on-mismatch.

## Realization (2026-07-02)

The provider-agnostic client is implemented via LangChain `init_chat_model`
(`LangChainModelClient`): `REPO_PILOT_MODEL_PROVIDER` + `REPO_PILOT_MODEL_ID`
(+temperature/max_tokens) select any supported provider with no code change; the
default `anthropic` (`claude-opus-4-8`) ships in core, others via extras. The CLI
builds the client and passes it to the graph; the seam is gated (fires only when
deterministic planning finds nothing) and degrades to deterministic-only if no key
/ provider package is available. `ReplayModelClient` keeps it token-free in tests.

## Consequences

- Model swap is a config change, not a code change — satisfies the portability
  requirement.
- LangGraph is adopted now to avoid a later migration as agent autonomy grows;
  its cost (dependency weight, version churn) is accepted.
- Guardrail from ADR-0004 holds: LangGraph orchestrates and proposes but does not
  adjudicate truth — the sandbox and deterministic tools remain the oracle.
- Cross-provider structured-output reliability is weaker than native tool-use; the
  client abstraction must normalize this, and seams must validate + retry.
