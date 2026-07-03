"""compile(runbook) lowers a Runbook to a compose project (ADR-0003), golden-tested."""

from pathlib import Path

from repo_pilot.compose import compile_components, compile_compose, render_compose

GOLDEN = Path(__file__).parent / "golden" / "express-compose.yaml"

EXPRESS_RUNBOOK = {
    "schema_version": "v1",
    "id": "node_npm_start",
    "status": "candidate",
    "repo": {"url": "https://github.com/org/repo", "commit": "abc123"},
    "runtime": {"image": "node:20-bookworm", "workdir": "/workspace/repo"},
    "steps": {
        "setup": [{"command": "npm install"}],
        "start": [{"command": "npm start", "expected_ports": [3000]}],
    },
    "healthcheck": {"strategy": "http", "url_candidates": ["/health", "/"]},
}


def test_compile_matches_golden_for_express_runbook():
    compose = compile_compose(EXPRESS_RUNBOOK)
    assert render_compose(compose) == GOLDEN.read_text()


def test_app_service_is_hardened_non_root_with_limits():
    app = compile_compose(EXPRESS_RUNBOOK)["services"]["app"]
    assert app["user"] == "1000:1000"  # non-root numeric uid:gid
    assert not app["user"].startswith("0")
    assert app["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["mem_limit"] and app["pids_limit"] and app["cpus"]


def test_compiled_compose_declares_dependency_services_with_wait():
    runbook = {
        **EXPRESS_RUNBOOK,
        "services": [{
            "name": "postgres",
            "image": "postgres:16",
            "env": {"POSTGRES_DB": "app"},
            "healthcheck": {"type": "command", "command": "pg_isready -U app"},
        }],
    }
    compose = compile_compose(runbook)
    pg = compose["services"]["postgres"]
    assert pg["image"] == "postgres:16"
    # the service gets a compose healthcheck, and the app waits for it to be healthy
    assert pg["healthcheck"]["test"] == ["CMD-SHELL", "pg_isready -U app"]
    assert compose["services"]["app"]["depends_on"] == {
        "postgres": {"condition": "service_healthy"}
    }


# --- component model (#37/#38) ---

COMPONENTS = [
    {
        "name": "db",
        "image": "postgres:16",
        "env": {"POSTGRES_PASSWORD": "app"},
        "oracle": {"type": "native-cmd", "command": "pg_isready -U app"},
    },
    {
        "name": "backend",
        "image": "python:3.11-bookworm",
        "workdir": "/workspace/repo",
        "command": "uvicorn app:app --host 0.0.0.0 --port 8000",
        "ports": [8000],
        "env": {"DATABASE_URL": "postgresql://app:app@db:5432/app"},
        "depends_on": ["db"],
        "oracle": {"type": "http", "port": 8000, "path": "/health"},
    },
]


def test_compile_components_lays_out_every_component_as_a_service():
    compose = compile_components(COMPONENTS)
    assert set(compose["services"]) == {"db", "backend"}
    be = compose["services"]["backend"]
    assert be["command"] == ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port 8000"]
    assert be["ports"] == [{"target": 8000}]
    assert be["environment"]["DATABASE_URL"].endswith("@db:5432/app")


def test_compile_components_waits_for_dependencies_via_healthcheck():
    compose = compile_components(COMPONENTS)
    # db has a native-cmd healthcheck; backend waits for it to be healthy
    assert compose["services"]["db"]["healthcheck"]["test"] == ["CMD-SHELL", "pg_isready -U app"]
    assert compose["services"]["backend"]["depends_on"] == {
        "db": {"condition": "service_healthy"}
    }


def test_compile_components_hardens_app_components_but_not_image_deps():
    compose = compile_components(COMPONENTS)
    # backend runs repo code (has a command) -> full hardening
    assert compose["services"]["backend"]["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in compose["services"]["backend"]["security_opt"]
    # db is a trusted managed image (no repo command) -> resource limits only
    assert "cap_drop" not in compose["services"]["db"]
    assert "mem_limit" in compose["services"]["db"]


def test_reproduce_compose_bakes_repo_code_components_portably():
    from repo_pilot.compose import reproduce_compose

    components = [
        {"name": "db", "image": "postgres:16",
         "oracle": {"type": "native-cmd", "command": "pg_isready"}},
        {"name": "backend", "image": "python:3.11", "workdir": "/workspace/repo",
         "command": "uvicorn app:app", "ports": [8000], "depends_on": ["db"],
         "oracle": {"type": "http", "port": 8000, "path": "/health"}},
    ]
    compose = reproduce_compose(components, repo_context="./repo")
    backend = compose["services"]["backend"]
    # repo-code component builds from the cloned repo (portable, no absolute paths)
    assert backend["build"]["context"] == "./repo"
    assert "COPY . /workspace/repo" in backend["build"]["dockerfile_inline"]
    assert "FROM python:3.11" in backend["build"]["dockerfile_inline"]
    assert "image" not in backend  # baked, not pulled
    # managed dependency image is untouched (still pulled by image)
    assert compose["services"]["db"]["image"] == "postgres:16"
    assert "build" not in compose["services"]["db"]
