"""DockerSandboxExecutor orchestration, with the compose CLI stubbed (no Docker).

Exercises the real start()/stop() logic — compose-file writing, published-port
parsing, log capture — by pointing compose_cmd at a shell stub.
"""

import os
import stat
from pathlib import Path

import pytest

from repo_pilot.compose import compile_compose
from repo_pilot.executor import DockerSandboxExecutor, DockerUnavailable

RUNBOOK = {
    "schema_version": "v1",
    "id": "node_npm_start",
    "status": "candidate",
    "repo": {"url": "x", "commit": "x"},
    "runtime": {"image": "node:20-bookworm", "workdir": "/workspace/repo"},
    "steps": {"start": [{"command": "npm start", "expected_ports": [3000]}]},
    "healthcheck": {"strategy": "http"},
}

STUB = """#!/usr/bin/env bash
case "$*" in
  *"port app 3000"*) echo "0.0.0.0:49999" ;;
  *" logs"*|*"logs --no-color"*) echo "fake app started" ;;
esac
exit 0
"""


def _make_stub(tmp_path: Path) -> str:
    script = tmp_path / "fakecompose.sh"
    script.write_text(STUB)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_start_writes_compose_parses_ports_and_captures_logs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    executor = DockerSandboxExecutor(compose_cmd=["bash", _make_stub(tmp_path)])
    sandbox = executor.start(compile_compose(RUNBOOK), repo_dir=str(repo))
    try:
        assert sandbox.ports == {3000: 49999}
        assert "fake app started" in sandbox.logs
        # the repo was copied in via a generated Dockerfile (no bind mount)
        assert (repo / "Dockerfile.repopilot").is_file()
    finally:
        sandbox.stop()


def test_misconfigured_compose_command_raises_clear_error(tmp_path):
    # a compose cmd that drops the `compose` subcommand -> Docker rejects our flags;
    # we must surface a clear config error, not a cryptic docker usage dump.
    repo = tmp_path / "repo"
    repo.mkdir()
    stub = tmp_path / "bad.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "unknown shorthand flag: \'p\' in -p" >&2\n'
        'echo "Usage:  docker [OPTIONS] COMMAND" >&2\n'
        "exit 1\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    executor = DockerSandboxExecutor(compose_cmd=["bash", str(stub)])
    with pytest.raises(DockerUnavailable, match="REPO_PILOT_COMPOSE_CMD"):
        executor.start(compile_compose(RUNBOOK), repo_dir=str(repo))


def test_missing_compose_binary_raises_docker_unavailable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    executor = DockerSandboxExecutor(compose_cmd=["repo-pilot-no-such-binary"])
    try:
        raised = False
        executor.start(compile_compose(RUNBOOK), repo_dir=str(repo))
    except DockerUnavailable:
        raised = True
    assert raised
