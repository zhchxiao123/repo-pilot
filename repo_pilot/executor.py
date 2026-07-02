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

from repo_pilot.compose import render_compose, with_repo_build


def http_fetch(
    host_port: int, path: str, timeout: float = 3.0, host: str = "127.0.0.1"
) -> tuple[int | None, str | None]:
    """Real HTTP GET. Returns (status, body); (None, None) if unreachable.

    An HTTP error response (404, 500, ...) is a reachable server, so its status is
    returned (body may be None); only connection/timeout failures yield (None, None).
    """
    url = f"http://{host}:{host_port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError):
        return None, None


def http_status(
    host_port: int, path: str, timeout: float = 3.0, host: str = "127.0.0.1"
) -> int | None:
    """Real HTTP GET probe. Returns the status code, or None if unreachable."""
    return http_fetch(host_port, path, timeout=timeout, host=host)[0]


@runtime_checkable
class RunningSandbox(Protocol):
    """A started sandbox: published ports, captured logs, and an HTTP probe."""

    ports: dict[int, int]  # container port -> host port

    @property
    def logs(self) -> str:
        """Captured container logs (may be queried lazily)."""

    def http_get(self, host_port: int, path: str, timeout: float = 3.0) -> int | None:
        """Return the HTTP status for a GET, or None if unreachable."""

    def fetch(
        self, host_port: int, path: str, timeout: float = 3.0
    ) -> tuple[int | None, str | None]:
        """Return (status, body) for a GET; (None, None) if unreachable."""

    def stop(self) -> None: ...


@runtime_checkable
class SandboxExecutor(Protocol):
    def start(
        self, compose: dict, repo_dir: str | None = None
    ) -> RunningSandbox: ...


class _FakeSandbox:
    def __init__(
        self,
        ports: dict[int, int],
        responses: dict[str, int],
        logs: str,
        bodies: dict[str, str],
    ):
        self.ports = ports
        self.responses = responses
        self.logs = logs
        self.bodies = bodies

    def http_get(self, host_port: int, path: str, timeout: float = 3.0) -> int | None:
        return self.responses.get(path)

    def fetch(
        self, host_port: int, path: str, timeout: float = 3.0
    ) -> tuple[int | None, str | None]:
        return self.responses.get(path), self.bodies.get(path)

    def stop(self) -> None:
        pass


class FakeSandboxExecutor:
    """Executor test double: returns canned ports, logs, and HTTP responses."""

    def __init__(
        self,
        ports: dict[int, int] | None = None,
        responses: dict[str, int] | None = None,
        logs: str = "started",
        bodies: dict[str, str] | None = None,
    ):
        self.ports = ports or {}
        self.responses = responses or {}
        self.logs = logs
        self.bodies = bodies or {}

    def start(self, compose: dict, repo_dir: str | None = None) -> RunningSandbox:
        return _FakeSandbox(
            dict(self.ports), dict(self.responses), self.logs, dict(self.bodies)
        )


class DockerUnavailable(RuntimeError):
    """Raised when the Docker CLI is not available on the host."""


# Subprocess timeouts (s): generous for `up` (image pull + install), tight for the
# short query/teardown commands. Guards against a hung Docker CLI.
_UP_TIMEOUT = 600
_CMD_TIMEOUT = 60


def _compose_works(cmd: list[str]) -> bool:
    try:
        return subprocess.run(
            [*cmd, "version"], capture_output=True, timeout=15
        ).returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def default_compose_cmd(env: str | None = None, works=_compose_works) -> list[str]:
    """Resolve the compose command: explicit env wins, else auto-detect the v2
    plugin (`docker compose`) or the standalone binary (`docker-compose`)."""
    env = env if env is not None else os.environ.get("REPO_PILOT_COMPOSE_CMD")
    if env:
        return env.split()
    if works(["docker", "compose"]):
        return ["docker", "compose"]
    if works(["docker-compose"]):
        return ["docker-compose"]
    return ["docker", "compose"]  # default; a clear error is raised later if unusable


_COMPOSE_MISCONFIG_MARKERS = (
    "unknown shorthand flag",
    "is not a docker command",
    "'compose' is not a docker command",
    "unknown flag",
)


def _looks_like_compose_misconfig(text: str) -> bool:
    return any(m in text for m in _COMPOSE_MISCONFIG_MARKERS)


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


_PROBE_IMAGE = "curlimages/curl:latest"


class _DockerSandbox:
    def __init__(
        self,
        compose_cmd: list[str],
        docker_cmd: list[str],
        project: str,
        compose_file: Path,
        workdir: Path,
        ports: dict[int, int],
        startup_log: str,
    ):
        self._compose_cmd = compose_cmd
        self._docker_cmd = docker_cmd
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
        return self.fetch(host_port, path, timeout=timeout)[0]

    def fetch(
        self, host_port: int, path: str, timeout: float = 3.0
    ) -> tuple[int | None, str | None]:
        # Probe from a throwaway container on the daemon's host network: published
        # ports live in the daemon's network namespace, not necessarily on our
        # localhost (true under rootless / VM-backed daemons). Works universally.
        url = f"http://127.0.0.1:{host_port}{path}"
        try:
            result = subprocess.run(
                [
                    *self._docker_cmd, "run", "--rm", "--network", "host", _PROBE_IMAGE,
                    "-s", "-m", str(int(timeout) or 1), "-w", "\n%{http_code}", url,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # unreachable / probe couldn't run — callers expect None, not a raise
            return None, None
        if result.returncode != 0 and not result.stdout:
            return None, None
        body, _, code = result.stdout.rpartition("\n")
        status = int(code) if code.isdigit() and code != "000" else None
        return status, (body or None)

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
            compose_cmd = default_compose_cmd()
        self._compose_cmd = compose_cmd
        # the plain docker command (for one-off probe containers), derived from the
        # compose command so a sudo/wrapper prefix is preserved.
        if compose_cmd and compose_cmd[-1] == "compose":
            self._docker_cmd = compose_cmd[:-1]  # docker compose -> docker
        elif compose_cmd and compose_cmd[-1] == "docker-compose":
            self._docker_cmd = [*compose_cmd[:-1], "docker"]  # [sudo] docker-compose -> [sudo] docker
        else:
            self._docker_cmd = ["docker"]

    def start(self, compose: dict, repo_dir: str | None = None) -> RunningSandbox:
        build = repo_dir is not None
        if repo_dir is not None:
            app_spec = compose.get("services", {}).get("app", {})
            image = app_spec.get("image", "debian:stable-slim")
            app_workdir = app_spec.get("working_dir", "/workspace/repo")
            dockerfile = "Dockerfile.repopilot"
            # Copy the repo into the image (streamed over the API — no shared FS
            # needed, unlike bind mounts). Runs as root in the hardened container
            # (cap_drop ALL + no-new-privileges + limits) so it can write its own
            # layer; HOME is set for tooling caches. Trade-off recorded in ADR-0007.
            (Path(repo_dir) / dockerfile).write_text(
                f"FROM {image}\nWORKDIR {app_workdir}\nCOPY . {app_workdir}\n"
            )
            compose = with_repo_build(compose, repo_dir, dockerfile)
            app = compose.get("services", {}).get("app")
            if app is not None:
                app["user"] = "0:0"
                env = app.setdefault("environment", {})
                if isinstance(env, dict):
                    env.setdefault("HOME", "/tmp")

        workdir = Path(tempfile.mkdtemp(prefix="repo-pilot-"))
        compose_file = workdir / "compose.generated.yaml"
        compose_file.write_text(render_compose(compose))
        project = "rp_" + uuid.uuid4().hex[:12]
        base = ["-p", project, "-f", str(compose_file)]

        up_args = [*base, "up", "-d"] + (["--build"] if build else [])
        up = _run_compose(self._compose_cmd, workdir, up_args, timeout=_UP_TIMEOUT)
        startup_log = up.stdout + up.stderr

        # A misconfigured compose command (e.g. REPO_PILOT_COMPOSE_CMD="docker",
        # dropping the `compose` subcommand) makes Docker reject our flags. That's a
        # config error, not a repairable app failure — surface it clearly.
        if up.returncode != 0 and _looks_like_compose_misconfig(startup_log):
            raise DockerUnavailable(
                f"the compose command {' '.join(self._compose_cmd)!r} was rejected by "
                "Docker — set REPO_PILOT_COMPOSE_CMD to a valid Docker Compose "
                "invocation (e.g. 'docker compose'). Detail: "
                + " ".join(startup_log.split())[:200]
            )

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
            self._compose_cmd,
            self._docker_cmd,
            project,
            compose_file,
            workdir,
            ports,
            startup_log,
        )
