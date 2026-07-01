"""Security envelope: hardening fields, egress policy, dummy env, redaction (ADR-0007)."""

from repo_pilot.security import (
    METADATA_CIDR,
    PRIVATE_CIDRS,
    default_security,
    dummy_env,
    egress_policy,
    redact,
    service_hardening,
)


def test_default_security_is_safe():
    sec = default_security()
    assert sec["egress"] == "block_private"
    assert sec["allow_metadata"] is False
    assert sec["isolation"] is True


def test_egress_default_blocks_private_and_metadata():
    policy = egress_policy(default_security())
    assert set(PRIVATE_CIDRS) <= set(policy["block"])  # metadata is within 169.254/16
    assert policy["allow"] == []


def test_allow_private_egress_still_blocks_metadata():
    sec = {**default_security(), "egress": "allow_private"}
    policy = egress_policy(sec)
    assert METADATA_CIDR in policy["block"]
    assert all(c not in policy["block"] for c in ("10.0.0.0/8", "192.168.0.0/16"))


def test_allow_metadata_carves_it_out():
    policy = egress_policy({**default_security(), "allow_metadata": True})
    assert METADATA_CIDR in policy["allow"]


def test_no_isolation_blocks_nothing():
    policy = egress_policy({**default_security(), "isolation": False})
    assert policy["block"] == [] and policy["allow"] == []


def test_service_hardening_is_non_root_with_limits():
    h = service_hardening()
    assert h["user"] != "root" and h["user"]
    assert h["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in h["security_opt"]
    assert "mem_limit" in h and "pids_limit" in h and "cpus" in h


def test_dummy_env_from_example(tmp_path):
    (tmp_path / ".env.example").write_text("DATABASE_URL=\nLOG_LEVEL=info\n# comment\n")
    env = dummy_env(tmp_path)
    assert set(env) == {"DATABASE_URL", "LOG_LEVEL"}
    assert all(v for v in env.values())  # dummy, non-empty


def test_redact_scrubs_secrets():
    text = "token=abc123 password: hunter2\nAuthorization: Bearer xyz"
    out = redact(text)
    assert "abc123" not in out
    assert "hunter2" not in out
    assert "xyz" not in out
    assert "REDACTED" in out
