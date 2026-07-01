# Usage

Full reference for the `repo-pilot` CLI, its flags, environment variables, and
outputs. For a quickstart see the top-level [README](../README.md).

## Prerequisites

- **Python 3.11+**
- **Docker** with the **Compose v2 plugin** (`docker compose`). The tool builds and
  runs the target project in throwaway containers.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
# the `repo-pilot` console script is now on the venv's PATH
.venv/bin/repo-pilot --help
```

## Command

```
repo-pilot run REPO_URL [OPTIONS]
```

`REPO_URL` is anything `git clone` accepts that the machine can reach: a GitHub
URL, another remote, or a local path / `file://` URL.

### Options

| Option | Default | Meaning |
|--------|---------|---------|
| `--commit TEXT` | (default branch HEAD) | Pin analysis to an exact commit SHA |
| `--artifacts-root TEXT` | `artifacts` | Directory to write per-job artifacts under |
| `--allow-private-egress` | off | Allow the sandbox to reach private networks (`10/8`, `172.16/12`, `192.168/16`) |
| `--allow-metadata` | off | Allow the sandbox to reach the cloud metadata endpoint (`169.254.169.254`) |
| `--no-isolation` | off | Disable network egress isolation entirely |

> Security is **default-safe**: without flags, private + metadata egress is
> blocked. The flags are conscious opt-outs — see [Security](#security).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `REPO_PILOT_COMPOSE_CMD` | `docker compose` | The compose command to invoke. Set to `sudo docker compose` if your user needs sudo for Docker, or e.g. a podman-compose wrapper. |
| `REPO_PILOT_ARTIFACTS_ROOT` | `artifacts` | Default artifacts root (overridden by `--artifacts-root`). |
| `REPO_PILOT_MODEL_PROVIDER` | `anthropic` | Model provider for the (optional) LLM fallback seam. |
| `REPO_PILOT_MODEL_ID` | `claude-opus-4-8` | Model id for the LLM fallback seam. |
| `REPO_PILOT_SCHEMAS_DIR` | bundled `schemas/` | Override the JSON Schema directory. |

## Outputs

Each run creates `artifacts/<job-id>/` containing:

- **`report.md`** — the human-readable report:
  - Repository (URL, commit, default branch)
  - Runtime: chosen candidate + confidence, verified/failed status, healthcheck
    result, and **reproduce** commands
  - Test targets discovered
  - Smoke test results (with per-failure request + `curl` reproduce)
  - On failure: captured (redacted) logs
- **`runbook.yaml`** — the **Verified Runbook** (`schemas/runbook.schema.json`): the
  reusable, proven description of how to run the project, including the executed
  `verification` block (ports, healthcheck, logs, reproduce).
- **`repo-profile.json`** — detected languages / frameworks / package managers /
  entrypoints, with `evidence_refs`.
- **`evidence.jsonl`** — one JSON object per fact backing the conclusions.
- **`repo/`** — the cloned repository.

## Exit behavior

- Prints `Verified: True|False` and the report path.
- Exits non-zero with a clear message if Docker is unavailable.
- A repo whose only run path is its own compose file is reported as
  `deferred: needs-compose` (not a silent failure).

## Security

Untrusted repo code runs inside a hardened container:

- non-root, `cap_drop: ALL`, `no-new-privileges`, CPU/memory/pids limits + a job
  timeout;
- **no real secrets** — host environment is never passed through, and
  `.env.example` variables are filled with dummy values;
- **logs are redacted** (tokens/passwords/keys/authorization) before storage;
- **egress policy** default-blocks private + cloud-metadata ranges; public package
  registries remain reachable. Opt out per-run with the flags above.

> Note: the egress *policy* is computed and recorded on the Runbook; applying it as
> host firewall rules is a planned follow-up (see ADR-0007 / ADR-0013). The
> container hardening, secret handling, and redaction are enforced today.

## Scope (v1)

- Single-service **Node** web apps (Express, Vite).
- Not yet: other languages, multi-service / compose-native repos, service
  dependencies (postgres/redis), the automatic repair loop, and strong-oracle /
  API-contract / UI tests. These are on the roadmap — see
  [ARCHITECTURE.md](ARCHITECTURE.md).
