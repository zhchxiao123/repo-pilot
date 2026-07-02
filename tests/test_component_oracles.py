"""Post-up oracle verification adjudicates each component against the live sandbox.

Uses the fake executor's canned per-service ports / compose state / logs so the
whole oracle library is exercised with no Docker (ADR-0004 seam).
"""

from repo_pilot.component_oracles import verify_component
from repo_pilot.executor import FakeSandboxExecutor

NOSLEEP = {"sleep": lambda _s: None}


def _sandbox(**kwargs):
    return FakeSandboxExecutor(**kwargs).start({})


def test_http_oracle_passes_on_acceptable_status():
    sb = _sandbox(component_ports={"backend": {8000: 49152}}, responses={"/health": 200})
    comp = {"name": "backend", "oracle": {"type": "http", "port": 8000, "path": "/health"}}
    assert verify_component(comp, sb).passed


def test_http_oracle_fails_on_server_error():
    sb = _sandbox(component_ports={"backend": {8000: 49152}}, responses={"/health": 500})
    comp = {"name": "backend", "oracle": {"type": "http", "port": 8000, "path": "/health"}}
    res = verify_component(comp, sb)
    assert not res.passed and "500" in res.detail


def test_http_oracle_fails_when_no_port_published():
    sb = _sandbox(component_ports={}, responses={"/health": 200})
    comp = {"name": "backend", "oracle": {"type": "http", "port": 8000, "path": "/health"}}
    assert not verify_component(comp, sb).passed


def test_native_cmd_oracle_reads_compose_health():
    sb = _sandbox(states={"db": ("running", "healthy", None)})
    comp = {"name": "db", "oracle": {"type": "native-cmd", "command": "pg_isready"}}
    assert verify_component(comp, sb).passed


def test_native_cmd_oracle_fails_while_starting():
    sb = _sandbox(states={"db": ("running", "starting", None)})
    comp = {"name": "db", "oracle": {"type": "native-cmd", "command": "pg_isready"}}
    assert not verify_component(comp, sb).passed


def test_process_up_oracle():
    up = _sandbox(states={"w": ("running", None, None)})
    dead = _sandbox(states={"w": ("exited", None, 1)})
    comp = {"name": "w", "oracle": {"type": "process-up"}}
    assert verify_component(comp, up).passed
    assert not verify_component(comp, dead).passed


def test_log_ready_oracle_matches_pattern():
    sb = _sandbox(service_logs={"w": "boot\nlistening on queue\n"})
    comp = {"name": "w", "oracle": {"type": "log-ready", "pattern": "listening on queue"}}
    assert verify_component(comp, sb).passed


def test_exit_zero_family_requires_clean_exit():
    ok = _sandbox(states={"b": ("exited", None, 0)})
    bad = _sandbox(states={"b": ("exited", None, 2)})
    running = _sandbox(states={"b": ("running", None, None)})
    for otype in ("exit-zero", "build-succeeds", "tests-pass", "functional-smoke"):
        comp = {"name": "b", "oracle": {"type": otype}}
        assert verify_component(comp, ok).passed, otype
        assert not verify_component(comp, bad).passed, otype
        assert not verify_component(comp, running).passed, otype  # not done yet


def test_tcp_port_oracle_needs_published_port_and_running():
    ok = _sandbox(component_ports={"db": {5432: 55432}}, states={"db": ("running", None, None)})
    comp = {"name": "db", "oracle": {"type": "tcp-port", "port": 5432}}
    assert verify_component(comp, ok).passed


def test_unknown_oracle_type_fails_closed():
    sb = _sandbox()
    comp = {"name": "x", "oracle": {"type": "telepathy"}}
    assert not verify_component(comp, sb).passed


def test_oracle_polls_until_ready():
    # a component that becomes healthy only on the third look must still pass when
    # retries allow it (slow boot: install + migrate + serve).
    class FlakySandbox:
        def __init__(self):
            self.calls = 0

        def service_state(self, name):
            self.calls += 1
            return ("running", "healthy" if self.calls >= 3 else "starting", None)

    sb = FlakySandbox()
    comp = {"name": "db", "oracle": {"type": "native-cmd", "command": "pg_isready"}}
    slept = []
    res = verify_component(comp, sb, retries=5, sleep=lambda s: slept.append(s))
    assert res.passed and sb.calls == 3 and len(slept) == 2
