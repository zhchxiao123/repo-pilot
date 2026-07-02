"""Post-up oracle verification (#38/#39).

After the executor brings a component Run Plan up (``docker compose up -d``, which
already blocks on ``depends_on: service_healthy`` for compose-healthcheck
components), each component's readiness oracle is adjudicated here against the live
sandbox — never from an agent's assertion (ADR-0004). Truth comes from the probe:
an HTTP response, the container's compose state/health, its logs, or its exit code.

Each check is polled up to ``retries`` times so slow-booting components (install +
migrate + serve) get a fair chance before we call them failed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from repo_pilot.executor import RunningSandbox

DEFAULT_ACCEPTABLE_STATUS = [200, 201, 204, 301, 302, 400, 401, 403, 404]
_PER_REQUEST_TIMEOUT = 3.0


@dataclass(frozen=True)
class OracleResult:
    passed: bool
    detail: str = ""


def _host_port(sandbox: RunningSandbox, name: str, container_port: int | None) -> int | None:
    """The host port a component's container port is published on (or the first
    published port if the oracle didn't name one)."""
    published = sandbox.service_ports(name)
    if container_port is not None and container_port in published:
        return published[container_port]
    return next(iter(published.values()), None)


def _check_http(sandbox: RunningSandbox, name: str, oracle: dict) -> OracleResult:
    host_port = _host_port(sandbox, name, oracle.get("port"))
    if host_port is None:
        return OracleResult(False, "no published port to probe")
    path = oracle.get("path", "/")
    acceptable = oracle.get("acceptable_status") or DEFAULT_ACCEPTABLE_STATUS
    status, _ = sandbox.fetch(host_port, path, timeout=_PER_REQUEST_TIMEOUT)
    if status is not None and status in acceptable:
        return OracleResult(True, f"GET {path} -> {status}")
    return OracleResult(False, f"GET {path} -> {status}")


def _check_tcp_port(sandbox: RunningSandbox, name: str, oracle: dict) -> OracleResult:
    # A published port that the daemon actually mapped means the listener bound and
    # the port is reachable through the daemon's netns. Require the container running.
    host_port = _host_port(sandbox, name, oracle.get("port"))
    state, _, _ = sandbox.service_state(name)
    if host_port is not None and state == "running":
        return OracleResult(True, f"port published on {host_port}, container running")
    return OracleResult(False, f"port={host_port} state={state}")


def _check_native_cmd(sandbox: RunningSandbox, name: str, oracle: dict) -> OracleResult:
    # `up`/compose runs the healthcheck we emitted; we read its verdict. Health may
    # be None on images/daemons that don't surface it — fall back to "running".
    state, health, _ = sandbox.service_state(name)
    if health == "healthy" or (health is None and state == "running"):
        return OracleResult(True, f"health={health or state}")
    return OracleResult(False, f"health={health} state={state}")


def _check_process_up(sandbox: RunningSandbox, name: str, oracle: dict) -> OracleResult:
    state, _, _ = sandbox.service_state(name)
    return OracleResult(state == "running", f"state={state}")


def _check_log_ready(sandbox: RunningSandbox, name: str, oracle: dict) -> OracleResult:
    pattern = oracle.get("pattern") or oracle.get("contains") or ""
    logs = sandbox.service_logs(name)
    if pattern and pattern in logs:
        return OracleResult(True, f"log matched {pattern!r}")
    return OracleResult(False, f"log pattern {pattern!r} not found")


def _check_exit_zero(sandbox: RunningSandbox, name: str, oracle: dict) -> OracleResult:
    # exit-zero / build-succeeds / tests-pass / functional-smoke: the component runs
    # a command to completion; success is a clean exit. Still running == not done yet.
    state, _, code = sandbox.service_state(name)
    if state == "exited" and code == 0:
        return OracleResult(True, "exited 0")
    return OracleResult(False, f"state={state} exit_code={code}")


def _check_stdio_handshake(sandbox: RunningSandbox, name: str, oracle: dict) -> OracleResult:
    # Full JSON-RPC handshake over stdio needs a driver (MCP client); until that
    # lands (#43) the best available signal is that the process stayed up.
    state, _, _ = sandbox.service_state(name)
    return OracleResult(state == "running", f"state={state} (handshake not driven)")


_CHECKS: dict[str, Callable[[RunningSandbox, str, dict], OracleResult]] = {
    "http": _check_http,
    "tcp-port": _check_tcp_port,
    "native-cmd": _check_native_cmd,
    "process-up": _check_process_up,
    "log-ready": _check_log_ready,
    "exit-zero": _check_exit_zero,
    "build-succeeds": _check_exit_zero,
    "tests-pass": _check_exit_zero,
    "functional-smoke": _check_exit_zero,
    "stdio-handshake": _check_stdio_handshake,
}


def verify_component(
    component: dict,
    sandbox: RunningSandbox,
    *,
    retries: int = 0,
    poll_interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> OracleResult:
    """Adjudicate one component's oracle against the live sandbox, with polling."""
    oracle = component.get("oracle", {})
    check = _CHECKS.get(oracle.get("type", ""))
    if check is None:
        return OracleResult(False, f"unknown oracle type {oracle.get('type')!r}")
    name = component["name"]
    result = OracleResult(False, "not yet checked")
    for attempt in range(retries + 1):
        result = check(sandbox, name, oracle)
        if result.passed:
            return result
        if attempt < retries:
            sleep(poll_interval)
    return result
