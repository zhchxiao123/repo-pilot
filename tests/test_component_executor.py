"""The Docker executor bakes the repo into every app-like component and parses
compose state — the two pieces the component-verify path relies on."""

from repo_pilot.executor import _build_component_services, _parse_ps_json


def test_build_component_services_bakes_only_app_like_services(tmp_path):
    compose = {
        "services": {
            "backend": {
                "image": "python:3.11",
                "working_dir": "/app",
                "command": ["sh", "-c", "uvicorn app:app"],
            },
            "db": {"image": "postgres:16"},  # managed dep: no command/workdir
        }
    }
    out = _build_component_services(compose, str(tmp_path))

    backend = out["services"]["backend"]
    assert "image" not in backend  # replaced by a build context
    assert backend["build"] == {
        "context": str(tmp_path),
        "dockerfile": "Dockerfile.repopilot.backend",
    }
    assert backend["user"] == "0:0" and backend["environment"]["HOME"] == "/tmp"
    dockerfile = (tmp_path / "Dockerfile.repopilot.backend").read_text()
    assert "FROM python:3.11" in dockerfile and "COPY . /app" in dockerfile

    # the managed image is untouched — it pulls its own image, unhardened here
    assert out["services"]["db"] == {"image": "postgres:16"}
    # input compose is not mutated
    assert "build" not in compose["services"]["backend"]


def test_parse_ps_json_handles_ndjson_and_array():
    ndjson = '{"Service":"db","State":"running","Health":"healthy","ExitCode":0}\n' \
             '{"Service":"app","State":"exited","ExitCode":0}'
    array = '[{"Service":"db","State":"running"}]'
    assert [o["Service"] for o in _parse_ps_json(ndjson)] == ["db", "app"]
    assert _parse_ps_json(array)[0]["Service"] == "db"
    assert _parse_ps_json("") == []


def test_fake_sandbox_serves_per_service_state_and_ports():
    from repo_pilot.executor import FakeSandboxExecutor

    sb = FakeSandboxExecutor(
        component_ports={"backend": {8000: 49152}},
        states={"db": ("running", "healthy", None)},
        service_logs={"worker": "ready"},
    ).start({})
    assert sb.service_ports("backend") == {8000: 49152}
    assert sb.service_state("db") == ("running", "healthy", None)
    assert sb.service_state("missing") == ("running", None, None)
    assert sb.service_logs("worker") == "ready"
