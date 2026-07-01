"""compile(runbook) lowers a Runbook to a compose project (ADR-0003), golden-tested."""

from pathlib import Path

from repo_pilot.compose import compile_compose, render_compose

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


def test_compiled_compose_declares_dependency_services():
    runbook = {
        **EXPRESS_RUNBOOK,
        "services": [
            {"name": "postgres", "image": "postgres:16", "env": {"POSTGRES_DB": "app"}}
        ],
    }
    compose = compile_compose(runbook)
    assert "postgres" in compose["services"]
    assert compose["services"]["postgres"]["image"] == "postgres:16"
    assert compose["services"]["app"]["depends_on"] == ["postgres"]
