# ADR-0002 — Docker Compose as the sandbox substrate; always generate our own

Status: accepted
Date: 2026-07-01

## Context

§7.1 ranks Docker Compose the #1 runbook candidate, but §8.3 warns a plain Docker
worker can't safely run nested Docker. That tension only exists for a
*containerized* worker. Per ADR-0001, the v1 CLI runs directly on the host, so it
can drive `docker compose` against the host daemon to create sibling containers
with **zero nesting** — untrusted code still runs inside containers with no socket
access, honoring §8.2.

Compose gives one uniform model for single- and multi-container repos and provides
`depends_on`, healthchecks, networks, and `env_file` natively (otherwise
hand-rolled, cf. §9.2). The remaining risk is that a repo's own compose file may
carry dangerous directives (`privileged`, `network_mode: host`, host volumes,
docker.sock mounts).

## Decision

Compose is the sandbox execution substrate. The v1 CLI drives `docker compose`
against the host daemon. We **always synthesize our own `compose.generated.yaml`**
and never execute a repo's compose file verbatim. A repo's compose file is treated
as high-value **evidence** (service names, images, ports, env, `depends_on`) that
seeds generation. Single-service repos also get a generated 1-service compose file
for a uniform codepath.

Every generated service carries, by construction: non-root `user`,
`cap_drop: [ALL]`, resource limits, `read_only` where feasible, and no host
mounts / no host network / no socket.

## Consequences

- Security is allowlist-by-construction — no fragile denylist sanitizer needed.
- Honors §4.4 ("默认不信任仓库代码") literally.
- One execution codepath for 1-or-N services.
- Repos with complex, correct compose files may be reproduced imperfectly by the
  generator, lowering success on the compose-heavy / §25 "复杂度高" bucket.
- Requires `docker compose` (v2) on the host — a v1 install prerequisite.
- Repos whose test harness itself runs Docker (e.g. testcontainers) are deferred
  in v1 (would need nested Docker).
