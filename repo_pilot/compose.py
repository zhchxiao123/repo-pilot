"""Compile a Runbook into a Docker Compose project (ADR-0003).

`compile_compose` is a pure, deterministic function — the only place compose is
produced, regenerated on every attempt. Security hardening (non-root, cap_drop,
resource limits, egress) is layered on in a later slice (ADR-0007).
"""

from __future__ import annotations

import copy
from typing import Any

import yaml

from repo_pilot.oracles import compose_healthcheck
from repo_pilot.security import resource_limits_only, service_hardening

_COMPONENT_RESOURCES = {"cpu": 2, "memory": "4g", "pids": 512}

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
    dep_hardening = resource_limits_only(hardening)

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


def compile_components(components: list[dict]) -> dict:
    """Compile a Run Plan's components into a multi-service compose project (#37/#38).

    Every component is a first-class service. A component that runs repo code (has a
    ``command``) is untrusted → full hardening; a pure managed image (no command,
    e.g. a database) is trusted → resource limits only (ADR-0017). A component whose
    oracle maps to a compose healthcheck gets one; dependents wait for it to be
    healthy (else merely started). The repo build/mount for app components is layered
    on by the executor (#38); this stays a pure function.
    """
    hardening = service_hardening(_COMPONENT_RESOURCES)
    dep_hardening = resource_limits_only(hardening)
    healthy = {c["name"] for c in components if compose_healthcheck(c["oracle"])}

    services: dict[str, Any] = {}
    for comp in components:
        runs_repo_code = "command" in comp
        service: dict[str, Any] = {
            "image": comp["image"],
            **(hardening if runs_repo_code else dep_hardening),
        }
        if "workdir" in comp:
            service["working_dir"] = comp["workdir"]
        if "command" in comp:
            service["command"] = ["sh", "-c", comp["command"]]
        if comp.get("env"):
            service["environment"] = dict(comp["env"])
        if comp.get("ports"):
            service["ports"] = [{"target": p} for p in comp["ports"]]
        hc = compose_healthcheck(comp["oracle"])
        if hc:
            service["healthcheck"] = hc
        deps = comp.get("depends_on") or []
        if deps:
            service["depends_on"] = {
                d: {"condition": "service_healthy" if d in healthy else "service_started"}
                for d in deps
            }
        services[comp["name"]] = service

    return {"services": services}


def render_compose(compose: dict) -> str:
    return yaml.safe_dump(compose, default_flow_style=False, sort_keys=True)


def reproduce_compose(components: list[dict], repo_context: str = "./repo") -> dict:
    """A portable compose for *reproduction* (not the executor's runtime compose).

    Each repo-code component (has a ``command``) is baked from the cloned repo via
    a relative build context + an inline Dockerfile, so a user can:

        git clone <url> repo        # into <repo_context>, beside this compose file
        docker compose -f compose.generated.yaml up --build

    and actually run the app. Managed dependency images (db/cache, no command) keep
    their ``image`` and are pulled. This differs from the runtime compose the
    executor builds (which streams an absolute build context to the daemon) — here
    the context is relative so the recipe is portable.
    """
    compose = compile_components(components)
    for comp in components:
        if "command" not in comp:
            continue  # managed dependency image — pulled, not built
        service = compose["services"][comp["name"]]
        image = service.get("image", "debian:stable-slim")
        workdir = comp.get("workdir", "/workspace/repo")
        service.pop("image", None)
        service["build"] = {
            "context": repo_context,
            "dockerfile_inline": f"FROM {image}\nWORKDIR {workdir}\nCOPY . {workdir}\n",
        }
    return compose


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
