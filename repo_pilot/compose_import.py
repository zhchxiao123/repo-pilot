"""Controlled compose import (plan Phase 2, Tasks 2.1/2.2).

Converts a *safe subset* of a target repo's compose file into a canonical
``RunPlan``. The target's compose file is evidence, never executed verbatim
(ADR-0002): the plan compiles through our own ``compile_components`` path, so
sandbox hardening and oracle adjudication stay ours.

Three honest outcomes:

- a ``RunPlan`` — every service mapped into the supported subset;
- ``deferred_reason="unsafe-compose"`` — the file asks for host power we will
  never grant (docker socket, host namespaces, absolute mounts, contexts
  escaping the repo);
- ``deferred_reason="needs-compose"`` — beyond the first slice (extends,
  env_file, ``${...}`` interpolation, unknown fields, build-only services whose
  runtime lives in a Dockerfile — that's Phase 3).

Service classification (Task 2.2): a service with a (repo-internal) build
context or an explicit command is **repo-code** — it runs the cloned repo,
untrusted, workdir defaulting to ``/workspace/repo``. An image-only service is
a **managed dependency** (postgres/redis/... get a ``native-cmd`` oracle from
their healthcheck, else ``process-up``).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from repo_pilot.confidence import confidence
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, infer_shape

COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")

_UNSAFE = "unsafe-compose"
_NEEDS = "needs-compose"

# Compose service fields the first slice maps into RunComponent.
_SUPPORTED_FIELDS = frozenset(
    {"image", "build", "command", "ports", "environment", "depends_on",
     "healthcheck", "working_dir"}
)
# Benign fields safely dropped (with a warning): they affect lifecycle/naming,
# not how the system comes up. `volumes` lands here only after the safety scan.
_IGNORED_FIELDS = frozenset({"restart", "container_name", "labels", "expose", "init", "volumes"})

# Well-known managed dependency images (matched on the image basename).
_DEPENDENCY_IMAGES = frozenset(
    {"postgres", "postgresql", "redis", "mysql", "mariadb", "mongo", "mongodb",
     "rabbitmq", "elasticsearch", "memcached", "nats", "kafka", "zookeeper", "minio"}
)

_DEFAULT_WORKDIR = "/workspace/repo"
_ACCEPTABLE_STATUS = [200, 204, 301, 302, 404]

# command first-token -> runtime image, for repo-code services that build from
# the repo (their compose declares no runnable image of its own).
_RUNTIME_IMAGES = {
    "python": "python:3.11", "python3": "python:3.11", "pip": "python:3.11",
    "flask": "python:3.11", "uvicorn": "python:3.11", "gunicorn": "python:3.11",
    "django-admin": "python:3.11",
    "node": "node:20-bookworm", "npm": "node:20-bookworm", "npx": "node:20-bookworm",
    "yarn": "node:20-bookworm", "pnpm": "node:20-bookworm",
    "go": "golang:1.22",
}

# repo manifest -> install step folded before the imported command, per runtime.
_INSTALL_STEPS = (
    ("requirements.txt", "python", "pip install -r requirements.txt"),
    ("pyproject.toml", "python", "pip install -e ."),
    ("package.json", "node", "npm install"),
)


@dataclass(frozen=True)
class ComposeImportResult:
    plan: RunPlan | None
    deferred_reason: str | None
    warnings: list[str] = field(default_factory=list)


def find_compose_file(repo_dir: str | Path) -> Path | None:
    repo_dir = Path(repo_dir)
    for name in COMPOSE_FILES:
        candidate = repo_dir / name
        if candidate.is_file():
            return candidate
    return None


def _as_command(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return None


def _as_env(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): "" if v is None else str(v) for k, v in value.items()}
    if isinstance(value, list):
        env: dict[str, str] = {}
        for item in value:
            key, _, val = str(item).partition("=")
            env[key] = val
        return env
    return {}


def _as_depends_on(value: object) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _target_ports(value: object, warnings: list[str], name: str) -> list[int]:
    """Container-side (target) ports from any compose port syntax."""
    ports: list[int] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, int):
            ports.append(item)
            continue
        if isinstance(item, dict):
            target = item.get("target")
            if isinstance(target, int):
                ports.append(target)
            continue
        target = str(item).split("/")[0].rsplit(":", 1)[-1]
        if target.isdigit():
            ports.append(int(target))
        else:
            warnings.append(f"{name}: skipped unsupported port syntax {item!r}")
    return ports


def _healthcheck_command(value: object) -> str | None:
    if not isinstance(value, dict) or value.get("disable"):
        return None
    test = value.get("test")
    if isinstance(test, str):
        return test if test != "NONE" else None
    if isinstance(test, list) and test:
        head, *rest = [str(t) for t in test]
        if head == "NONE":
            return None
        if head == "CMD-SHELL":
            return " ".join(rest)
        if head == "CMD":
            return " ".join(rest)
        return " ".join([head, *rest])
    return None


def _volume_safety(volumes: object) -> str | None:
    """None if every mount is benign (named volume / relative path), else why
    the compose is unsafe. Docker socket and absolute host paths never pass."""
    for item in volumes if isinstance(volumes, list) else []:
        if isinstance(item, dict):
            source = str(item.get("source", ""))
        else:
            source = str(item).split(":", 1)[0]
        if "docker.sock" in str(item):
            return "mounts the docker socket"
        if source.startswith("/"):
            return f"mounts absolute host path {source!r}"
    return None


def _build_context(build: object) -> tuple[str | None, str | None]:
    """(context, why-unsafe). A context is only safe when it stays inside the repo."""
    context = build.get("context", ".") if isinstance(build, dict) else str(build)
    normalized = posixpath.normpath(str(context))
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        return None, f"build context {context!r} escapes the repo"
    return normalized, None


def _unsafe_reason(name: str, svc: dict) -> str | None:
    """The first host-power request that makes this service unimportable."""
    if svc.get("privileged"):
        return f"{name}: privileged"
    for key in ("network_mode", "pid", "ipc"):
        if svc.get(key) == "host":
            return f"{name}: {key}=host"
    why = _volume_safety(svc.get("volumes"))
    if why:
        return f"{name}: {why}"
    if "build" in svc:
        _, why = _build_context(svc["build"])
        if why:
            return f"{name}: {why}"
    return None


def _has_interpolation(svc: dict) -> bool:
    """``${...}`` in any supported field needs .env handling we don't do yet."""
    def _scan(value: object) -> bool:
        if isinstance(value, str):
            return "${" in value
        if isinstance(value, dict):
            return any(_scan(v) for v in value.values())
        if isinstance(value, list):
            return any(_scan(v) for v in value)
        return False

    return any(_scan(svc.get(f)) for f in _SUPPORTED_FIELDS)


def _is_dependency_image(image: str) -> bool:
    basename = image.split("/")[-1].split(":", 1)[0].lower()
    return basename in _DEPENDENCY_IMAGES


def _runtime_image(command: str) -> str | None:
    first = command.split()[0] if command.split() else ""
    return _RUNTIME_IMAGES.get(first)


def _install_prefix(repo_dir: Path, image: str) -> str | None:
    """The dependency-install step the service's own Dockerfile would have run.
    Our sandbox runs the command in a base image with the repo copied in, so the
    install is folded in front when the repo carries a manifest for the runtime."""
    for manifest, runtime, step in _INSTALL_STEPS:
        if runtime in image and (repo_dir / manifest).is_file():
            return step
    return None


def import_compose(
    repo_dir: str | Path,
    profile: dict | None = None,
    evidence: list[dict] | None = None,
) -> ComposeImportResult:
    """Import ``repo_dir``'s compose file as a canonical ``RunPlan`` (or defer)."""
    repo_dir = Path(repo_dir)
    compose_file = find_compose_file(repo_dir)
    if compose_file is None:
        return ComposeImportResult(plan=None, deferred_reason=None)

    warnings: list[str] = []
    try:
        data = yaml.safe_load(compose_file.read_text())
    except yaml.YAMLError as exc:
        return ComposeImportResult(None, _NEEDS, [f"unparseable compose file: {exc}"])
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict) or not services:
        return ComposeImportResult(None, _NEEDS, ["compose file declares no services"])

    # Safety first: any single unsafe service condemns the whole file — a partial
    # import would silently run a different system than the repo describes.
    for name, svc in services.items():
        if not isinstance(svc, dict):
            return ComposeImportResult(None, _NEEDS, [f"{name}: unsupported service form"])
        reason = _unsafe_reason(str(name), svc)
        if reason:
            return ComposeImportResult(None, _UNSAFE, [reason])

    components: list[RunComponent] = []
    repo_code = 0
    for name, svc in services.items():
        name = str(name)
        unknown = set(svc) - _SUPPORTED_FIELDS - _IGNORED_FIELDS
        if unknown:
            return ComposeImportResult(
                None, _NEEDS, [f"{name}: unsupported fields {sorted(unknown)}"]
            )
        if _has_interpolation(svc):
            return ComposeImportResult(
                None, _NEEDS, [f"{name}: ${{...}} interpolation needs env handling"]
            )
        for ignored in sorted(_IGNORED_FIELDS & set(svc)):
            warnings.append(f"{name}: dropped unsupported-but-benign field {ignored!r}")

        image = svc.get("image")
        command = _as_command(svc.get("command"))
        ports = _target_ports(svc.get("ports"), warnings, name)
        env = _as_env(svc.get("environment"))
        depends_on = _as_depends_on(svc.get("depends_on"))
        healthcheck_cmd = _healthcheck_command(svc.get("healthcheck"))

        if "build" in svc or command is not None:
            # repo-code (Task 2.2): builds from the repo and/or runs an explicit
            # command; executes the cloned checkout, untrusted.
            if command is None:
                return ComposeImportResult(
                    None, _NEEDS,
                    [f"{name}: builds from the repo but its runtime command lives "
                     "in a Dockerfile (Dockerfile-first support is a later slice)"],
                )
            image = image or _runtime_image(command)
            if image is None:
                return ComposeImportResult(
                    None, _NEEDS, [f"{name}: cannot infer a runtime image for {command!r}"]
                )
            install = _install_prefix(repo_dir, image)
            oracle = (
                Oracle(type="http", port=ports[0], path="/health",
                       acceptable_status=list(_ACCEPTABLE_STATUS))
                if ports else Oracle(type="process-up")
            )
            components.append(
                RunComponent(
                    name=name, image=image,
                    workdir=svc.get("working_dir") or _DEFAULT_WORKDIR,
                    command=f"{install} && {command}" if install else command,
                    env=env, ports=ports, depends_on=depends_on, oracle=oracle,
                )
            )
            repo_code += 1
        else:
            # managed dependency: pulled image, never repo code (Task 2.2)
            if not _is_dependency_image(str(image)):
                warnings.append(
                    f"{name}: image-only service {image!r} is not a recognized "
                    "dependency; treating as a managed dependency"
                )
            oracle = (
                Oracle(type="native-cmd", command=healthcheck_cmd)
                if healthcheck_cmd else Oracle(type="process-up")
            )
            components.append(
                RunComponent(name=name, image=str(image), env=env, ports=ports,
                             depends_on=depends_on, oracle=oracle)
            )

    if repo_code == 0:
        return ComposeImportResult(
            None, _NEEDS,
            [*warnings, "no repo-code service (dependency images only)"],
        )

    refs = [
        e["id"] for e in (evidence or [])
        if e.get("kind") == "compose_service" and "id" in e
    ]
    plan = RunPlan(
        id="compose_import",
        shape=infer_shape(components),
        components=components,
        confidence=confidence(["compose_service"]) if refs else None,
        evidence_refs=refs,
        repo=(profile or {}).get("repo"),
        source="compose-import",
        rationale=f"imported from {compose_file.name} (controlled subset)",
    )
    return ComposeImportResult(plan=plan, deferred_reason=None, warnings=warnings)
