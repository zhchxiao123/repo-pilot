"""Compile a Runbook into a Docker Compose project (ADR-0003).

`compile_compose` is a pure, deterministic function — the only place compose is
produced, regenerated on every attempt. Security hardening (non-root, cap_drop,
resource limits, egress) is layered on in a later slice (ADR-0007).
"""

from __future__ import annotations

import copy
from typing import Any

import yaml

from repo_pilot.security import service_hardening

_STEP_PHASES = ("setup", "build", "migrate", "start")


def iter_step_commands(runbook: dict) -> list[str]:
    """The runbook's step commands in phase order (setup, build, migrate, start)."""
    steps = runbook.get("steps", {})
    return [step["command"] for phase in _STEP_PHASES for step in steps.get(phase, [])]


def compile_compose(runbook: dict) -> dict:
    steps = runbook.get("steps", {})
    commands = iter_step_commands(runbook)
    resources = runbook.get("runtime", {}).get("resources")
    hardening = service_hardening(resources)
    # dependency services keep their image's own user (e.g. postgres)
    # Dependency services are trusted official images (postgres/redis/...) that we
    # choose, not untrusted repo code — so they get resource limits only. Stripping
    # caps / no-new-privileges would break images that setuid at startup (postgres).
    dep_hardening = {k: v for k, v in hardening.items() if k in ("mem_limit", "cpus", "pids_limit")}

    app: dict[str, Any] = {
        "image": runbook["runtime"]["image"],
        "working_dir": runbook["runtime"]["workdir"],
        "command": ["sh", "-c", " && ".join(commands)],
        **hardening,
    }

    ports = [
        {"target": port}
        for step in steps.get("start", [])
        for port in step.get("expected_ports", [])
    ]
    if ports:
        app["ports"] = ports

    generated_env = runbook.get("env", {}).get("generated", {})
    if generated_env:
        app["environment"] = dict(generated_env)

    dependency_services = runbook.get("services", [])
    if dependency_services:
        # wait for each dependency: service_healthy if it declares a healthcheck,
        # otherwise service_started (best we can do without one).
        app["depends_on"] = {
            svc["name"]: {
                "condition": "service_healthy"
                if svc.get("healthcheck", {}).get("command")
                else "service_started"
            }
            for svc in dependency_services
        }

    services: dict[str, Any] = {"app": app}
    for svc in dependency_services:
        compiled: dict[str, Any] = {"image": svc["image"], **dep_hardening}
        if svc.get("env"):
            compiled["environment"] = dict(svc["env"])
        svc_ports = [{"target": p["container"]} for p in svc.get("ports", [])]
        if svc_ports:
            compiled["ports"] = svc_ports
        hc = svc.get("healthcheck", {})
        if hc.get("command"):
            compiled["healthcheck"] = {
                "test": ["CMD-SHELL", hc["command"]],
                "interval": "5s",
                "timeout": "5s",
                "retries": 20,
            }
        services[svc["name"]] = compiled

    return {"services": services}


def render_compose(compose: dict) -> str:
    return yaml.safe_dump(compose, default_flow_style=False, sort_keys=True)


def with_repo_build(compose: dict, context_dir: str, dockerfile: str) -> dict:
    """Return a copy of ``compose`` where the app is built (code copied in) rather
    than bind-mounted.

    Building streams the context to the daemon over the API, so it works even when
    the daemon does not share the host filesystem (unlike bind mounts). No-op if
    there is no app service with a working_dir.
    """
    app = compose.get("services", {}).get("app")
    if not app or "working_dir" not in app:
        return compose
    result = copy.deepcopy(compose)
    built = result["services"]["app"]
    built.pop("image", None)
    built["build"] = {"context": context_dir, "dockerfile": dockerfile}
    return result


def with_repo_mount(compose: dict, repo_dir: str) -> dict:
    """Return a copy of ``compose`` with the cloned repo bind-mounted into the app.

    The host path is a runtime binding, so it is applied here rather than baked into
    the pure Runbook->compose lowering (ADR-0003). No-op if there is no app service
    with a working_dir.
    """
    app = compose.get("services", {}).get("app")
    if not app or "working_dir" not in app:
        return compose
    result = copy.deepcopy(compose)
    result["services"]["app"]["volumes"] = [f"{repo_dir}:{app['working_dir']}"]
    return result
