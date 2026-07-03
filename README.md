# repo-pilot

Turn any Git repository into a **Verified Runbook** plus a smoke-test report —
automatically. Point it at a repo and it figures out how to install, build, and
start the project, **proves it actually starts in a sandbox**, then runs first-pass
online tests and writes a reproducible report.

The core idea: don't let an LLM *guess* how to run a project. Extract run signals
from evidence (CI, README, Dockerfile, package manifests), plan candidate ways to
run it, and **verify by actually executing in an isolated container** — the sandbox,
not the model, decides what's true.

repo-pilot verifies the **runnable shape** of a repo, not just web services
(ADR-0019). Success is shape-specific:

- **service** — an Express app comes up and answers its HTTP oracle.
- **cli** — a command runs to a clean exit (functional-smoke).
- **library** — the test suite passes (tests-pass).
- **build** — the build succeeds (build-succeeds).
- **multi-component** — every component (db + backend + …) reaches its oracle.
- **docs** — honestly reported as not runnable.

```
GitHub repo ─▶ Profile ─▶ Runbook candidates ─▶ Sandbox verify ─▶ Verified Runbook
                                                                        │
                                                        Target discovery ▼ Smoke tests ─▶ Report
```

> **v1 scope:** single-service **Node** web apps (Express, Vite), depth-first. More
> languages (Python, Java, Go) and capabilities (service deps, repair loop, richer
> tests) are planned. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Quickstart

### 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .          # installs the `repo-pilot` CLI
```

Requires **Python 3.11+** and **Docker with the Compose v2 plugin** (`docker compose`).

### 2. Run

```bash
repo-pilot run https://github.com/org/repo
repo-pilot run https://github.com/org/repo --commit <sha>
repo-pilot run /path/to/local/repo --artifacts-root ./artifacts
```

If your user isn't in the `docker` group (needs `sudo`) or you use a wrapper,
point the executor at your compose command:

```bash
export REPO_PILOT_COMPOSE_CMD="sudo docker compose"
repo-pilot run /path/to/repo
```

### 3. Read the results

The console prints whether the app verified and where the report is. Everything
lands under `artifacts/<job-id>/`:

| File | What it is |
|------|------------|
| `report.md` | Human-readable report: detection, Verified Runbook, healthcheck, **reproduce** commands, test targets, smoke results |
| `runbook.yaml` | The **Verified Runbook** — the reusable core asset: how to run the project, proven |
| `repo-profile.json` | Detected languages / frameworks / package managers, with evidence references |
| `evidence.jsonl` | Every fact backing the conclusions (traceable) |
| `repo/` | The cloned repository |

Full CLI reference, flags, and env vars: **[docs/USAGE.md](docs/USAGE.md)**.

---

## Example

```console
$ repo-pilot run /path/to/express-app
Job: job-4de3fb051954
Repo: /path/to/express-app
Verified: True
Report: artifacts/job-4de3fb051954/report.md
```

`report.md`:

```markdown
## Runtime
- Candidate: node_npm_start (confidence 0.88)
- Status: verified
- Healthcheck: 200 at http://127.0.0.1:32783/health

### Reproduce
    git clone <repo> repo
    cd repo
    npm install
    npm start

## Test targets
- GET /health (healthcheck)
- GET / (healthcheck)

## Smoke tests
- 3/3 passed
```

---

## How it works (short version)

A fixed pipeline of phases, each grounded in real evidence or real execution:

1. **Clone** — shallow clone + optional commit checkout.
2. **Profile** — deterministic static analysis → `repo-profile.json` + `evidence.jsonl`.
3. **Plan** — build candidate Runbooks from evidence, ranked by a deterministic
   confidence score. (Falls back to an LLM only when deterministic extraction finds
   nothing — and even then the sandbox still verifies it.)
4. **Verify** — compile the Runbook to a Docker Compose project, start it in an
   isolated sandbox, and confirm it's up with a real healthcheck.
5. **Discover** — find HTTP test targets (OpenAPI, else healthcheck paths).
6. **Test** — run weak-oracle smoke tests (no 5xx / crash / stack-trace / secret leak).
7. **Report** — write the report and the Verified Runbook.

**Safety:** untrusted repo code runs in a hardened container (`cap_drop: ALL`,
`no-new-privileges`, CPU/memory/pids limits), no real secrets are ever injected
(`.env.example` → dummy values), and logs are redacted. Egress policy is
default-safe (private + metadata ranges blocked) with explicit opt-out flags.

The guiding principles (evidence-first, sandbox-as-oracle, Runbook-as-source-of-
truth) and every design decision are recorded as ADRs in
[docs/adr/](docs/adr/README.md).

---

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q                    # unit suite (no Docker)
.venv/bin/mypy repo_pilot --ignore-missing-imports
```

Docker-backed **integration tests** are marked `integration` and excluded by
default. To run them (needs Docker):

```bash
REPO_PILOT_COMPOSE_CMD="sudo docker compose" \
  .venv/bin/python -m pytest -m integration -o addopts=""
```

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the module map, the
determinism boundary, and the test seams.

## License

See [LICENSE](LICENSE).
