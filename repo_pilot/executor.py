"""Sandbox Executor seam (ADR-0002/0004).

The executor is the single boundary that touches Docker and the network. This
module defines the interface plus a fake implementation used to drive the pipeline
with no Docker. The real Docker-backed implementation lands in a later slice.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RunningSandbox(Protocol):
    """A started sandbox: published ports, captured logs, and an HTTP probe."""

    ports: dict[int, int]  # container port -> host port
    logs: str

    def http_get(self, host_port: int, path: str, timeout: float = 3.0) -> int | None:
        """Return the HTTP status for a GET, or None if unreachable."""

    def stop(self) -> None: ...


@runtime_checkable
class SandboxExecutor(Protocol):
    def start(self, compose: dict) -> RunningSandbox: ...


class _FakeSandbox:
    def __init__(self, ports: dict[int, int], responses: dict[str, int], logs: str):
        self.ports = ports
        self.responses = responses
        self.logs = logs

    def http_get(self, host_port: int, path: str, timeout: float = 3.0) -> int | None:
        return self.responses.get(path)

    def stop(self) -> None:
        pass


class FakeSandboxExecutor:
    """Executor test double: returns canned ports, logs, and HTTP responses."""

    def __init__(
        self,
        ports: dict[int, int] | None = None,
        responses: dict[str, int] | None = None,
        logs: str = "started",
    ):
        self.ports = ports or {}
        self.responses = responses or {}
        self.logs = logs

    def start(self, compose: dict) -> RunningSandbox:
        return _FakeSandbox(dict(self.ports), dict(self.responses), self.logs)
