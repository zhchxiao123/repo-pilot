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
| `--no-llm` | off | Disable the LLM fallback seam (run fully deterministically) |

> Security is **default-safe**: without flags, private + metadata egress is
> blocked. The flags are conscious opt-outs — see [Security](#security).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `REPO_PILOT_COMPOSE_CMD` | auto-detected | The compose command to invoke. **Auto-detected**: the `docker compose` (v2 plugin) or standalone `docker-compose` binary. Set explicitly to override, e.g. `sudo docker compose` (sudo) or a podman-compose wrapper. |
| `REPO_PILOT_ARTIFACTS_ROOT` | `artifacts` | Default artifacts root (overridden by `--artifacts-root`). |
| `REPO_PILOT_MODEL_PROVIDER` | `anthropic` | Provider for the LLM fallback seam. Any LangChain `init_chat_model` provider (`anthropic`, `openai`, `google_genai`, `bedrock`, …); install that provider's package (see extras). |
| `REPO_PILOT_MODEL_ID` | `claude-opus-4-8` | Model id for the LLM fallback seam. |
| `REPO_PILOT_MODEL_TEMPERATURE` | `0.0` | Sampling temperature. |
| `REPO_PILOT_MODEL_MAX_TOKENS` | `2048` | Max output tokens. |
| `REPO_PILOT_MODEL_BASE_URL` | — | Custom endpoint (OpenAI-compatible gateway/proxy, vLLM, Ollama, internal LLM gateway). |
| `REPO_PILOT_MODEL_API_KEY` | — | Explicit API key override (else the provider's default env var below is used). |
| `<PROVIDER>_API_KEY` | — | The provider's API key, read at call time (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). |
| `REPO_PILOT_SCHEMAS_DIR` | bundled `schemas/` | Override the JSON Schema directory. |

### LLM fallback seam

Planning is deterministic (evidence-first). The **only** LLM use is a *gated
fallback*: when deterministic extraction finds no run command (e.g. instructions
live only in README prose), the model proposes one — and the sandbox still verifies
it (ADR-0004). The model layer is provider-agnostic via LangChain
`init_chat_model` (ADR-0005): switch providers with `REPO_PILOT_MODEL_PROVIDER` +
`REPO_PILOT_MODEL_ID`, no code change.

- Default provider `anthropic` ships in core; add others via extras:
  `pip install -e '.[openai]'` / `'.[google]'`.
- If no API key is set (or the seam can't be built), the run **degrades to
  deterministic-only** — no crash. Use `--no-llm` to disable it explicitly.

**Custom / self-hosted endpoints.** Point at any OpenAI-compatible server (vLLM,
Ollama, LM Studio, an internal gateway) via `REPO_PILOT_MODEL_BASE_URL` +
`REPO_PILOT_MODEL_API_KEY`:

```bash
pip install -e '.[openai]'
export REPO_PILOT_MODEL_PROVIDER=openai
export REPO_PILOT_MODEL_ID=your-model-name
export REPO_PILOT_MODEL_BASE_URL=https://your-gateway/v1
export REPO_PILOT_MODEL_API_KEY=sk-...      # or a placeholder for keyless local servers
```
(Any provider `init_chat_model` supports works the same way; `base_url`/`api_key`
are forwarded to it. Note: the plan agent needs a model that supports tool calling.)

## Outputs

Each run creates `artifacts/<job-id>/` containing:

- **`report.md`** — the human-readable report:
  - Repository (URL, commit, default branch)
  - **Outcome**: the shape-specific verdict (`verified` / `failed` /
    `not_runnable` / ...), the detected **shape** (service, cli, library, build,
    batch, multi-component, docs), and — when verified — what it was **exercised
    by** (the oracle that adjudicated success)
  - **Run Plan**: the components that ran (image, command, oracle)
  - **Reproduce** commands, at shape-appropriate granularity (a single-component
    shape reproduces via its command; a multi-component system via `docker
    compose up`)
  - Test targets discovered; smoke test results (with per-failure request +
    `curl` reproduce)
  - On failure: captured (redacted) logs

  repo-pilot does not only verify web services. A **cli** is verified by running a
  real command to a clean exit, a **library** by running its test suite, a
  **build** by building successfully — non-web repos succeed by being *exercised*,
  not by starting a server.
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

## Scope — what works today

**Deterministic planning paths** (no LLM needed):

- **Node** (Express, Vite, plain `http` servers — `package.json` scripts)
- **Python** (Flask, FastAPI/uvicorn, Django — requirements/pyproject + entrypoints)
- **Go** (`go.mod`: `go run`/`go build`, CLIs and HTTP services)
- **Make** (Makefile-driven C/build repos via a build-tool pseudo-ecosystem)

Stacks the rules don't recognize fall through to the **plan agent** (LLM
fallback, sandbox-verified — see above). Either way the outcome is one of the
**run shapes**: `service`, `multi_component_service`, `cli`, `library`,
`build`, `batch`, or `docs` (not runnable) — success is shape-specific: a
service must answer HTTP, a cli/batch must run to a clean exit, a library must
pass its test suite, a build must build.

**Delivered:** the automatic **repair loop** (ADR-0012: on a failed verify,
diagnose rules-first / LLM-fallback, patch the RunPlan, retry — the sandbox
still adjudicates), **weak-oracle smoke tests** against verified services, and
the **eval harness** (`repo-pilot eval`, see
[eval-harness.md](eval-harness.md)) that scores verdict coverage over the
pinned 50-case manifest.

**Explicit boundaries** (reported honestly, not silently failed):

- **Compose-native repos**: a repo whose only run path is its own
  `docker-compose.yml` is reported `deferred: needs-compose`. The target's
  compose file is treated as *evidence*, never executed verbatim; a controlled
  compose import is the next planned slice (see the
  [coverage expansion plan](plans/2026-07-04-coverage-driven-runtime-expansion.md)).
- **Strong-oracle tests** (OpenAPI contract, UI tests): not yet — smoke tests
  are weak-oracle only. Downstream functional testing is out of scope by
  design: repo-pilot's boundary is bring-up — get the project running,
  machine-readably prove it's ready, and hand off to the consuming agent
  (ADR-0020).
- **Other ecosystems** (Java, Rust, Ruby/PHP/.NET): planned, gated on eval
  cases that justify them.

## Eval

```
repo-pilot eval eval/manifest.50.json --workdir artifacts/eval-runs
```

Sweeps a pinned manifest of repos through the full pipeline and scores how
often the verdict matches the expected one, with per-case artifacts and
failure clusters. Full reference: [eval-harness.md](eval-harness.md).
