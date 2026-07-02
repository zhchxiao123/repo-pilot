"""Security envelope (ADR-0007): default-safe with opt-out.

Provides the per-service hardening the compiler emits, the egress policy (public
allowed; private + metadata blocked by default), dummy env generation (no real
secrets), and log redaction. Egress *enforcement* (host firewall rules on the job
network) is applied by the Docker executor and covered by integration tests; the
policy itself is a pure, unit-tested function here.
"""

from __future__ import annotations

import re
from pathlib import Path

PRIVATE_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"]
METADATA_CIDR = "169.254.169.254/32"

_DEFAULT_MEMORY = "4g"
_DEFAULT_CPUS = 2.0
_DEFAULT_PIDS = 512
# Numeric non-root UID:GID — works in any image without a passwd entry. Applies to
# dependency services and the compile-level default; the executor runs the app
# container as root when it copies the repo in via `docker build` (ADR-0013).
_NON_ROOT_USER = "1000:1000"


def default_security() -> dict:
    return {"egress": "block_private", "allow_metadata": False, "isolation": True}


def egress_policy(security: dict) -> dict:
    """Return {block: [cidr], allow: [cidr]} for an accept-before-drop enforcer.

    `allow` entries are intentionally more-specific than `block` and MUST be
    applied first (accept /32 before drop /16) — the standard firewall model. So
    `allow_metadata` yields allow=[metadata/32] alongside block=[link-local/16],
    which the enforcer resolves to "metadata reachable, rest of link-local blocked".

    NOTE: this computes the policy only. Applying it as host firewall rules on the
    job's docker network requires a privileged Docker environment and is done by the
    executor there (integration); it is not enforced in the no-Docker path.
    """
    if not security.get("isolation", True):
        return {"block": [], "allow": []}

    block: list[str] = []
    allow: list[str] = []
    if security.get("egress", "block_private") == "block_private":
        block = list(PRIVATE_CIDRS)  # 169.254/16 (incl. metadata) is within this
    if security.get("allow_metadata", False):
        allow = [METADATA_CIDR]
    elif METADATA_CIDR not in block and not any(
        c.startswith("169.254.") for c in block
    ):
        block.append(METADATA_CIDR)  # metadata stays blocked even if private allowed
    return {"block": block, "allow": allow}


def service_hardening(resources: dict | None = None) -> dict:
    resources = resources or {}
    return {
        "user": _NON_ROOT_USER,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": resources.get("memory", _DEFAULT_MEMORY),
        "cpus": resources.get("cpu", _DEFAULT_CPUS),
        "pids_limit": resources.get("pids", _DEFAULT_PIDS),
    }


def resource_limits_only(hardening: dict) -> dict:
    """The subset of hardening safe for trusted managed images (ADR-0017): resource
    limits, but not cap_drop / no-new-privileges / non-root (which break images that
    setuid at startup, e.g. postgres)."""
    return {k: v for k, v in hardening.items() if k in ("mem_limit", "cpus", "pids_limit")}


def dummy_env(repo_dir: str | Path) -> dict[str, str]:
    """Generate dummy values for variables named in .env.example (§20.2)."""
    example = Path(repo_dir) / ".env.example"
    if not example.is_file():
        return {}
    env: dict[str, str] = {}
    for raw in example.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name:
            env[name] = "dummy"
    return env


_REDACT_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)(\S+)"),
    re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|key)\b(\s*[:=]\s*)(\S+)"),
]


def redact(text: str) -> str:
    out = text
    out = _REDACT_PATTERNS[0].sub(r"\1REDACTED", out)
    out = _REDACT_PATTERNS[1].sub(r"\1\2REDACTED", out)
    return out
