"""Readiness oracles (#39): which map to compose healthchecks vs post-up checks."""

from repo_pilot.oracles import ORACLE_TYPES, compose_healthcheck, is_post_up


def test_native_cmd_becomes_a_compose_healthcheck():
    hc = compose_healthcheck({"type": "native-cmd", "command": "pg_isready -U app"})
    assert hc["test"] == ["CMD-SHELL", "pg_isready -U app"]
    assert hc["retries"] >= 1 and "start_period" in hc


def test_post_up_oracles_have_no_compose_healthcheck():
    # these are verified out-of-band after `up` (executor's job, #38), not by compose
    for t in ("http", "tcp-port", "log-ready", "process-up", "exit-zero",
              "functional-smoke", "build-succeeds", "tests-pass", "stdio-handshake"):
        oracle = {"type": t, "port": 8000, "command": "x", "pattern": "y"}
        assert compose_healthcheck(oracle) is None
        assert is_post_up(oracle) is True


def test_native_cmd_is_not_post_up():
    assert is_post_up({"type": "native-cmd", "command": "pg_isready"}) is False


def test_oracle_type_registry_is_complete():
    assert {
        "http", "tcp-port", "native-cmd", "log-ready", "process-up",
        "stdio-handshake", "exit-zero", "functional-smoke",
        "build-succeeds", "tests-pass",
    } == ORACLE_TYPES
