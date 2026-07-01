"""Compile a Runbook into a Docker Compose project (ADR-0003).

`compile_compose` is a pure, deterministic function — the only place compose is
produced, regenerated on every attempt. Security hardening (non-root, cap_drop,
resource limits, egress) is layered on in a later slice (ADR-0007).
"""

from __future__ import annotations

from typing import Any

import yaml

_STEP_PHASES = ("setup", "build", "migrate", "start")


def iter_step_commands(runbook: dict) -> list[str]:
    """The runbook's step commands in phase order (setup, build, migrate, start)."""
    steps = runbook.get("steps", {})
    return [step["command"] for phase in _STEP_PHASES for step in steps.get(phase, [])]


def compile_compose(runbook: dict) -> dict:
    steps = runbook.get("steps", {})
    commands = iter_step_commands(runbook)

    app: dict[str, Any] = {
        "image": runbook["runtime"]["image"],
        "working_dir": runbook["runtime"]["workdir"],
        "command": ["sh", "-c", " && ".join(commands)],
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
        app["depends_on"] = [svc["name"] for svc in dependency_services]

    services: dict[str, Any] = {"app": app}
    for svc in dependency_services:
        compiled: dict[str, Any] = {"image": svc["image"]}
        if svc.get("env"):
            compiled["environment"] = dict(svc["env"])
        svc_ports = [{"target": p["container"]} for p in svc.get("ports", [])]
        if svc_ports:
            compiled["ports"] = svc_ports
        services[svc["name"]] = compiled

    return {"services": services}


def render_compose(compose: dict) -> str:
    return yaml.safe_dump(compose, default_flow_style=False, sort_keys=True)
