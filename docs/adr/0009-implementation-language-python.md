# ADR-0009 — Implementation language: Python

Status: accepted
Date: 2026-07-01

## Context

§16.1 recommends Python (file analysis, rule engines, LLM orchestration; mature
YAML/TOML/AST/HTTP/Playwright/pytest ecosystem). ADR-0005 adopts LangGraph, which
is Python-native.

## Decision

The system is implemented in **Python**. This is effectively forced by LangGraph
(ADR-0005) and matches §16.1.

## Consequences

- Direct access to LangGraph/LangChain, tomllib/pyyaml, tree-sitter, httpx,
  Playwright, and schemathesis (deterministic tools per ADR-0004).
- Runtime-image selection for target repos (Node/Java/Go) is independent of the
  host implementation language — the sandbox runs the repo's own runtime.
