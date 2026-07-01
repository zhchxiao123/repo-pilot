"""HTTP Target Discovery: from the live app's OpenAPI, else healthcheck paths (§10.2).

Targets are grounded in the running app / parsed schema, never invented.
"""

import json

from repo_pilot.discovery import discover_targets
from repo_pilot.executor import FakeSandboxExecutor

OPENAPI = json.dumps(
    {"paths": {"/users": {"get": {}, "post": {}}, "/health": {"get": {}}}}
)


def test_discovers_endpoints_from_openapi():
    sandbox = FakeSandboxExecutor(
        ports={8000: 49000},
        responses={"/openapi.json": 200},
        bodies={"/openapi.json": OPENAPI},
    ).start({})
    targets = discover_targets(sandbox)
    signatures = {(t["method"], t["path"]) for t in targets}
    assert ("GET", "/users") in signatures
    assert ("POST", "/users") in signatures
    assert all(t["source"] == "openapi" for t in targets)


def test_falls_back_to_healthcheck_paths_without_openapi():
    sandbox = FakeSandboxExecutor(
        ports={3000: 49152}, responses={"/": 200}
    ).start({})
    targets = discover_targets(sandbox, fallback_paths=["/health", "/"])
    paths = {t["path"] for t in targets}
    assert paths == {"/health", "/"}
    assert all(t["source"] == "healthcheck" and t["method"] == "GET" for t in targets)


def test_targets_carry_base_url_with_host_port():
    sandbox = FakeSandboxExecutor(ports={3000: 49152}, responses={"/": 200}).start({})
    targets = discover_targets(sandbox, fallback_paths=["/"])
    assert targets[0]["base_url"] == "http://127.0.0.1:49152"
