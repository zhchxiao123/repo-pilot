# ADR-0017 — Environment provisioning: services, env, and the hardening trust boundary

Status: accepted
Date: 2026-07-02

## Context

Most real "won't start" failures aren't the wrong command — they're a missing
**service** (postgres/redis), a missing **env var**, or the wrong runtime. To move
coverage toward 90%, the pipeline must provision the environment, not just tweak
commands.

## Decision

- **The plan agent declares dependencies.** `submit_plan` candidates may include
  `services` (`[{name, image, env, healthcheck}]`) and `env` (vars the app needs,
  referencing services by name as host). These map to the Runbook's `services` /
  `env.generated`.
- **Compose waits for dependencies.** `compile_compose` emits a compose healthcheck
  for each dependency that declares one and sets the app's
  `depends_on: {svc: {condition: service_healthy}}` so the app starts only once its
  deps are ready. The executor's `docker compose up` brings up the whole project.
- **Repair can add a database.** On DB-connection failures in the logs (rule
  `provision postgres service`), repair adds a postgres service + a default
  `DATABASE_URL` — a fast common-case fix before the LLM path.
- **Hardening trust boundary (important).** Full hardening (`cap_drop: ALL`,
  `no-new-privileges`, non-root, limits) applies to the **app** container — that's
  where untrusted repo code runs. **Dependency services** are trusted official
  images we choose (postgres/redis/…); they get **resource limits only**. Stripping
  caps / no-new-privileges breaks images that `setuid` at startup (postgres refuses
  to become healthy), which would defeat provisioning. Deps still run with resource
  limits, no host mounts, and under the egress policy.

## Consequences

- Repos needing a database now verify: proven end-to-end in real Docker — the agent
  provisions postgres, compose waits for it healthy, a Flask app connects on
  `/health`, verifies, smoke 3/3.
- The security envelope is unchanged for untrusted code (the app); trusted managed
  services are functional-but-bounded. This is a deliberate, auditable relaxation.
- Runtime-version selection is already handled by the agent choosing the base image.
