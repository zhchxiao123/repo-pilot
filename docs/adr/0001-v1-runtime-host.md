# ADR-0001 — v1 runtime host: single-host local Docker CLI

Status: accepted
Date: 2026-07-01

## Context

The plan doc is split across three delivery shapes: CLI+Worker (§26), FastAPI +
Redis Streams + Docker Worker (§16.2), and a CoderFleet worker task type (§21).
The choice gates the sandbox isolation model (§8.3), whether Compose runbooks can
run at all (§7.1), and the tech stack (Docker SDK vs K8s Job). The MVP (§17)
explicitly excludes multi-tenancy and concurrency.

## Decision

v1 ships as a **CLI that drives a local Docker daemon on a single host**. No
FastAPI, no Redis Streams, no Kubernetes. Untrusted repo commands run in local
Docker containers on that one machine. The core pipeline is built as a library so
the `POST /jobs` service shape (§17.2) can wrap it later without rework.

## Consequences

- No concurrent jobs and no remote submission in v1; the eval set (§19) is run as
  a batch loop.
- Queue/controller tier (Redis, FastAPI) is deferred to a later phase — do not
  build it before a repo can be started and verified end-to-end.
- The core must stay transport-agnostic (no CLI-only assumptions leaking into the
  pipeline) so the service wrapper is additive. Enforced in a later ADR on the
  core/library boundary.
