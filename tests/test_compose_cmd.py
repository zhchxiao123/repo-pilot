"""Auto-detection of the compose command: v2 plugin or standalone docker-compose."""

from repo_pilot.executor import DockerSandboxExecutor, default_compose_cmd


def test_explicit_env_wins():
    assert default_compose_cmd(env="sudo docker compose") == ["sudo", "docker", "compose"]


def test_prefers_v2_plugin_when_available():
    assert default_compose_cmd(env="", works=lambda c: c == ["docker", "compose"]) == [
        "docker", "compose"
    ]


def test_falls_back_to_standalone_docker_compose():
    # v2 plugin absent, standalone present (the user's case)
    got = default_compose_cmd(env="", works=lambda c: c == ["docker-compose"])
    assert got == ["docker-compose"]


def test_defaults_when_nothing_detected():
    assert default_compose_cmd(env="", works=lambda c: False) == ["docker", "compose"]


def test_probe_docker_cmd_derives_from_standalone_compose():
    # [sudo] docker-compose -> [sudo] docker for the one-off probe container
    ex = DockerSandboxExecutor(compose_cmd=["docker-compose"])
    assert ex._docker_cmd == ["docker"]
    ex2 = DockerSandboxExecutor(compose_cmd=["sudo", "docker-compose"])
    assert ex2._docker_cmd == ["sudo", "docker"]
    ex3 = DockerSandboxExecutor(compose_cmd=["sudo", "docker", "compose"])
    assert ex3._docker_cmd == ["sudo", "docker"]
