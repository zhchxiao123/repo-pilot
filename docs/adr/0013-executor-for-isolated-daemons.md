# ADR-0013 — Executor for isolated Docker daemons: build-context + in-network probe

Status: accepted
Date: 2026-07-02

## Context

Running the real `DockerSandboxExecutor` against an actual daemon (first time —
earlier slices had no Docker in CI) revealed three problems, two of them
environmental (the daemon does not share the host's filesystem or network
namespace — true under rootless / VM-backed daemons):

1. **`user: sandbox` fails** — the image has no such user
   (`unable to find user sandbox`). Even a numeric non-root UID can't write the
   bind-mounted, host-owned clone.
2. **Bind mounts don't propagate host files** — the daemon's filesystem differs
   from ours, so a bind-mounted clone appears empty inside the container
   (`ENOENT package.json`).
3. **Published ports aren't on our localhost** — they live in the daemon's network
   namespace; probing `127.0.0.1:<hostport>` from our process fails, though a
   container with `--network host` reaches them.

## Decision

- **Copy the repo in via `docker build`, not a bind mount.** The executor writes a
  generated `Dockerfile` (`FROM <image>; WORKDIR; COPY . <workdir>`) and points the
  compose app service at `build: {context, dockerfile}` (`up --build`). The build
  context is streamed to the daemon over the API, so it works without a shared
  filesystem. `with_repo_build` replaces `with_repo_mount` in the run path.
- **Run the app as root inside the still-hardened container.** cap_drop ALL,
  no-new-privileges, and resource limits remain; only the user is root so it can
  write its own image layer / caches. Under rootless Docker, container-root maps to
  the unprivileged host user anyway. (Amends ADR-0007's non-root default.)
- **Probe HTTP from a throwaway `curl` container on `--network host`.** The
  sandbox's `http_get`/`fetch` run `docker run --rm --network host curlimages/curl`
  so probes reach the daemon-side published ports. Works universally (slower than a
  direct localhost probe, acceptable for verify/smoke).

## Consequences

- The real end-to-end pipeline verifies on this environment: build → start →
  healthcheck → discovery → smoke, all green (integration tests pass).
- Trade-offs: image build per run (slower than mount); a probe container per HTTP
  request (slower than urllib). Acceptable for a verification tool.
- Non-root-by-default (ADR-0007) is relaxed for the app container to root-in-a-
  hardened-container; the rest of the envelope stands.
- `http_status`/`http_fetch` (direct urllib probes) remain for potential
  shared-network deployments but are no longer on the executor's critical path.
