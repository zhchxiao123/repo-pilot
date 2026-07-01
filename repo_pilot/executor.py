"""Sandbox Executor seam (ADR-0002/0004).

The executor is the single boundary that touches Docker and the network. This
module defines the interface plus a fake implementation used to drive the pipeline
with no Docker. The real Docker-backed implementation lands in a later slice.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

from repo_pilot.compose import render_compose, with_repo_mount


def http_status(
    host_port: int, path: str, timeout: float = 3.0, host: str = "127.0.0.1"
) -> int | None:
    """Real HTTP GET probe. Returns the status code, or None if unreachable.

    An HTTP error response (e.g. 404, 500) is a *reachable* server, so its status
    is returned; only connection/timeout failures yield None.
    """
    url = f"http://{host}:{host_port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return None


@runtime_checkable
class RunningSandbox(Protocol):
    """A started sandbox: published ports, captured logs, and an HTTP probe."""

    ports: dict[int, int]  # container port -> host port

    @property
    def logs(self) -> str:
        """Captured container logs (may be queried lazily)."""

    def http_get(self, host_port: int, path: str, timeout: float = 3.0) -> int | None:
        """Return the HTTP status for a GET, or None if unreachable."""

    def stop(self) -> None: ...


@runtime_checkable
class SandboxExecutor(Protocol):
    def start(
        self, compose: dict, repo_dir: str | None = None
    ) -> RunningSandbox: ...


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

    def start(self, compose: dict, repo_dir: str | None = None) -> RunningSandbox:
        return _FakeSandbox(dict(self.ports), dict(self.responses), self.logs)


class DockerUnavailable(RuntimeError):
    """Raised when the Docker CLI is not available on the host."""


# Subprocess timeouts (s): generous for `up` (image pull + install), tight for the
# short query/teardown commands. Guards against a hung Docker CLI.
_UP_TIMEOUT = 600
_CMD_TIMEOUT = 60


def _app_target_ports(compose: dict) -> list[int]:
    app = compose.get("services", {}).get("app", {})
    return [p["target"] for p in app.get("ports", []) if "target" in p]


def _run_compose(
    compose_cmd: list[str], workdir: Path, args: list[str], *, timeout: float
) -> subprocess.CompletedProcess:
    """Run a compose command, mapping a missing binary and a hang to results."""
    try:
        return subprocess.run(
            [*compose_cmd, *args],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DockerUnavailable(
            f"'{compose_cmd[0]}' not found — Docker is required (ADR-0002)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        out = out.decode(errors="replace") if isinstance(out, bytes) else out
        err = err.decode(errors="replace") if isinstance(err, bytes) else err
        return subprocess.CompletedProcess(
            exc.cmd, 124, out, err + f"\n[compose timed out after {timeout}s]"
        )


class _DockerSandbox:
    def __init__(
        self,
        compose_cmd: list[str],
        project: str,
        compose_file: Path,
        workdir: Path,
        ports: dict[int, int],
        startup_log: str,
    ):
        self._compose_cmd = compose_cmd
        self._project = project
        self._compose_file = compose_file
        self._workdir = workdir
        self.ports = ports
        self._startup_log = startup_log

    def _compose(self, *args: str) -> subprocess.CompletedProcess:
        # Always pass -p and -f so the project resolves regardless of cwd/filename.
        return _run_compose(
            self._compose_cmd,
            self._workdir,
            ["-p", self._project, "-f", str(self._compose_file), *args],
            timeout=_CMD_TIMEOUT,
        )

    @property
    def logs(self) -> str:
        result = self._compose("logs", "--no-color")
        return (self._startup_log + result.stdout + result.stderr).strip()

    def http_get(self, host_port: int, path: str, timeout: float = 3.0) -> int | None:
        return http_status(host_port, path, timeout=timeout)

    def stop(self) -> None:
        self._compose("down", "-v", "--remove-orphans")
        shutil.rmtree(self._workdir, ignore_errors=True)


class DockerSandboxExecutor:
    """Real executor: runs the generated compose against the local Docker daemon.

    The single boundary that touches Docker and the network (ADR-0002). Untrusted
    repo code runs inside containers with no socket access.
    """

    def __init__(self, compose_cmd: list[str] | None = None):
        if compose_cmd is None:
            env = os.environ.get("REPO_PILOT_COMPOSE_CMD")
            compose_cmd = env.split() if env else ["docker", "compose"]
        self._compose_cmd = compose_cmd

    def start(self, compose: dict, repo_dir: str | None = None) -> RunningSandbox:
        if repo_dir is not None:
            compose = with_repo_mount(compose, repo_dir)

        workdir = Path(tempfile.mkdtemp(prefix="repo-pilot-"))
        compose_file = workdir / "compose.generated.yaml"
        compose_file.write_text(render_compose(compose))
        project = "rp_" + uuid.uuid4().hex[:12]
        base = ["-p", project, "-f", str(compose_file)]

        up = _run_compose(
            self._compose_cmd, workdir, [*base, "up", "-d"], timeout=_UP_TIMEOUT
        )
        startup_log = up.stdout + up.stderr

        ports: dict[int, int] = {}
        for target in _app_target_ports(compose):
            mapped = _run_compose(
                self._compose_cmd,
                workdir,
                [*base, "port", "app", str(target)],
                timeout=_CMD_TIMEOUT,
            )
            lines = mapped.stdout.strip().splitlines()
            if mapped.returncode == 0 and lines:
                host = lines[-1].rsplit(":", 1)[-1].strip()
                if host.isdigit():
                    ports[target] = int(host)

        return _DockerSandbox(
            self._compose_cmd, project, compose_file, workdir, ports, startup_log
        )
