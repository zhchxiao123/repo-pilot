"""HTTP Target Discovery (§10.1, §10.2).

Against the live app: parse `/openapi.json` for endpoints if present, otherwise
fall back to the healthcheck candidate paths. Targets are grounded in the running
app / parsed schema — never invented (ADR-0004, §11.4).
"""

from __future__ import annotations

import json

from repo_pilot.executor import RunningSandbox

_DEFAULT_FALLBACK = ["/health", "/"]
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def discover_targets(
    sandbox: RunningSandbox, fallback_paths: list[str] | None = None
) -> list[dict]:
    for host_port in sandbox.ports.values():
        base_url = f"http://127.0.0.1:{host_port}"
        status, body = sandbox.fetch(host_port, "/openapi.json")
        if status == 200 and body:
            spec = _parse_openapi(body)
            if spec:
                return _openapi_targets(base_url, spec)

    fallback_paths = fallback_paths or _DEFAULT_FALLBACK
    targets: list[dict] = []
    for host_port in sandbox.ports.values():
        base_url = f"http://127.0.0.1:{host_port}"
        for path in fallback_paths:
            targets.append(
                {
                    "type": "http",
                    "base_url": base_url,
                    "method": "GET",
                    "path": path,
                    "source": "healthcheck",
                }
            )
        break  # fall back on the first published port only
    return targets


def _parse_openapi(body: str) -> dict | None:
    try:
        spec = json.loads(body)
    except json.JSONDecodeError:
        return None
    return spec if isinstance(spec, dict) and "paths" in spec else None


def _openapi_targets(base_url: str, spec: dict) -> list[dict]:
    targets: list[dict] = []
    for path, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method in methods:
            if method.lower() in _HTTP_METHODS:
                targets.append(
                    {
                        "type": "api",
                        "base_url": base_url,
                        "method": method.upper(),
                        "path": path,
                        "source": "openapi",
                    }
                )
    return targets
