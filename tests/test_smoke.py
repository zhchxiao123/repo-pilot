"""Weak-oracle smoke tests: generate from targets, run against the live app (§11.2)."""

from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.smoke import generate_smoke_tests, run_smoke_tests

TARGETS = [
    {"type": "http", "base_url": "http://127.0.0.1:49152", "method": "GET", "path": "/health", "source": "healthcheck"},
    {"type": "http", "base_url": "http://127.0.0.1:49152", "method": "GET", "path": "/", "source": "healthcheck"},
]


def test_generates_one_smoke_test_per_target_bound_to_it():
    tests = generate_smoke_tests(TARGETS)
    assert len(tests) == len(TARGETS)
    for t, target in zip(tests, TARGETS):
        assert t["method"] == "GET"
        assert t["target"] == target  # every test references a discovered target


def test_run_passes_on_non_5xx():
    sandbox = FakeSandboxExecutor(
        ports={3000: 49152}, responses={"/health": 200, "/": 404}
    ).start({})
    results = run_smoke_tests(sandbox, generate_smoke_tests(TARGETS))
    assert all(r["status"] == "passed" for r in results)  # 404 is not a crash


def test_run_fails_on_5xx_with_request_and_reproduce():
    sandbox = FakeSandboxExecutor(
        ports={3000: 49152}, responses={"/health": 500, "/": 200}
    ).start({})
    results = run_smoke_tests(sandbox, generate_smoke_tests(TARGETS))
    failed = [r for r in results if r["status"] == "failed"]
    assert len(failed) == 1
    f = failed[0]
    assert f["response"]["status"] == 500
    assert "/health" in f["request"]["url"]
    assert any("curl" in c for c in f["reproduce"])


def test_run_fails_on_invalid_json_body():
    sandbox = FakeSandboxExecutor(
        ports={3000: 49152},
        responses={"/health": 200, "/": 200},
        bodies={"/health": "{not valid json"},
    ).start({})
    results = run_smoke_tests(sandbox, generate_smoke_tests(TARGETS))
    assert any(
        r["status"] == "failed" and "/health" in r["request"]["url"] for r in results
    )


def test_run_fails_on_leaked_secret():
    sandbox = FakeSandboxExecutor(
        ports={3000: 49152},
        responses={"/health": 200, "/": 200},
        bodies={"/": "-----BEGIN RSA PRIVATE KEY-----\n..."},
    ).start({})
    results = run_smoke_tests(sandbox, generate_smoke_tests(TARGETS))
    assert any(
        r["status"] == "failed" and r["reason"] == "response leaked a secret"
        for r in results
    )


def test_run_fails_on_leaked_stack_trace():
    sandbox = FakeSandboxExecutor(
        ports={3000: 49152},
        responses={"/health": 200, "/": 200},
        bodies={"/health": "Traceback (most recent call last):\n  File ..."},
    ).start({})
    results = run_smoke_tests(sandbox, generate_smoke_tests(TARGETS))
    failed = [r for r in results if r["status"] == "failed"]
    assert any("/health" in r["request"]["url"] for r in failed)
