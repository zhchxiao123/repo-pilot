"""Fake Sandbox Executor drives the healthcheck with no Docker (ADR-0004 seam)."""

from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.healthcheck import run_healthcheck

SPEC = {
    "strategy": "http",
    "url_candidates": ["/health", "/"],
    "acceptable_status": [200, 204, 301, 302, 404],
}


def test_healthcheck_passes_on_first_acceptable_path():
    sandbox = FakeSandboxExecutor(ports={3000: 49152}, responses={"/health": 200}).start(
        {"services": {}}
    )
    result = run_healthcheck(sandbox, SPEC)
    assert result.passed
    assert result.status_code == 200
    assert "/health" in result.url
    assert "49152" in result.url


def test_healthcheck_fails_when_no_acceptable_response():
    sandbox = FakeSandboxExecutor(
        ports={3000: 49152}, responses={"/health": 500, "/": 503}
    ).start({"services": {}})
    result = run_healthcheck(sandbox, SPEC)
    assert not result.passed


def test_healthcheck_skips_unreachable_paths_and_tries_next():
    # /health is not answered (None), / returns 200 -> should still pass on /
    sandbox = FakeSandboxExecutor(ports={3000: 49152}, responses={"/": 200}).start(
        {"services": {}}
    )
    result = run_healthcheck(sandbox, SPEC)
    assert result.passed
    assert result.url.endswith("/")
