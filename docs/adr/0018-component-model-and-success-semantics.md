# 0018 — Component model and non-service success semantics

Status: accepted

## Context

A real project is usually a **system of components** — a frontend, a backend, a
database, a cache, a worker, an MCP server, a CLI — not a single HTTP process. The
original pipeline modelled "runs" as one app answering one HTTP healthcheck, which
under-serves full-stack repos and misjudges non-service repos (a CLI or a library
is not "unrunnable" — it just succeeds differently).

## Decision

**A repo is a Run Plan of N components. Each component declares a readiness
*oracle*. The system "runs" when every component reaches its oracle in dependency
order.** The sandbox adjudicates each oracle (ADR-0004); the agent proposes the
decomposition (ADR-0016).

Oracle library (`repo_pilot/oracles.py`, `component_oracles.py`):

| oracle | ready when | verified by |
|--------|-----------|-------------|
| `native-cmd` | a command in the image succeeds (`pg_isready`, `redis-cli ping`) | compose healthcheck + `depends_on: service_healthy` |
| `http` | an endpoint answers | external curl-container probe (post-up) |
| `tcp-port` | a port accepts connections | published-port + running (post-up) |
| `log-ready` | a line appears in logs | grep service logs (post-up) |
| `process-up` | it stays running | compose state (post-up) |
| `exit-zero` / `build-succeeds` / `tests-pass` / `functional-smoke` | it runs to a clean exit | compose state + exit code (post-up) |
| `stdio-handshake` | a protocol init succeeds | driver TBD (MCP fixture) — process-up interim |

Only `native-cmd` maps to a compose healthcheck (it uses the image's *own* tooling,
guaranteed present). The rest are verified **post-up**, out of band — so `up` stays
`-d` (not `--wait`): `depends_on` already gates start order, and this correctly
handles `exit-zero`/batch components that `up --wait` would flag as failed.

### Non-service success = being exercised

A non-service repo succeeds by being **run to a real result**, not by existing:

- **cli** — run a real subcommand (not just `--help`): `functional-smoke` / `exit-zero`
- **library** — run its test suite: `tests-pass`
- **batch** — run the job to completion: `exit-zero`
- **build / IaC** — run the build/validate (`make`, `docker build`, `terraform validate`): `build-succeeds`

The plan agent classifies honestly *and* proposes an exercise component with the
matching oracle; `candidates: []` is reserved for the truly unrunnable (docs-only,
or exercise undeterminable). A verified non-service is reported as a success with
its component verdicts, not as "not a service".

## Consequences

- The Runbook schema gains an additive `components[]` and `verification.components[]`;
  single-app runbooks are unchanged (a 1-component special case).
- The executor bakes the repo into every app-like component (own base image +
  Dockerfile), extending the ADR-0013 build-context approach to N services.
- Trust boundary (ADR-0017) is per-component: components that run repo code are
  fully hardened; managed images get resource limits only.
- Discovery/smoke over HTTP still targets the primary service; per-component smoke
  is future work.
