"""Readiness oracles for components (#39).

Each component in a Run Plan declares an oracle describing what "ready/succeeded"
means for it. Oracles split into two kinds:

- **compose-healthcheck** — expressible as a Docker Compose service healthcheck, so
  `docker compose up --wait` waits for it. Only ``native-cmd`` qualifies reliably:
  it runs a command using the image's *own* tooling (e.g. ``pg_isready`` in postgres,
  ``redis-cli ping`` in redis), which is guaranteed present in that image.
- **post-up** — verified out-of-band by the executor after `up` (the executor slice,
  #38): ``http`` (external probe — avoids needing curl inside the app image),
  ``tcp-port``, ``log-ready`` (grep logs), ``process-up`` (alive after a grace
  period), ``exit-zero``/``functional-smoke`` (run-and-check), ``build-succeeds``/
  ``tests-pass`` (run a command), and ``stdio-handshake`` (protocol init).
"""

from __future__ import annotations

ORACLE_TYPES = {
    "http",
    "tcp-port",
    "native-cmd",
    "log-ready",
    "process-up",
    "stdio-handshake",
    "exit-zero",
    "functional-smoke",
    "build-succeeds",
    "tests-pass",
}


def is_post_up(oracle: dict) -> bool:
    """True if this oracle is verified out-of-band after `up` (not by compose).

    Defined as the exact complement of ``compose_healthcheck`` so the two never
    disagree (e.g. a native-cmd oracle missing its command is post-up, not a
    third state)."""
    return compose_healthcheck(oracle) is None


def compose_healthcheck(oracle: dict) -> dict | None:
    """Map an oracle to a Docker Compose healthcheck, or None if it's post-up."""
    if oracle.get("type") == "native-cmd" and oracle.get("command"):
        return {
            "test": ["CMD-SHELL", oracle["command"]],
            "interval": "5s",
            "timeout": "5s",
            "retries": 20,
            "start_period": "3s",
        }
    return None
