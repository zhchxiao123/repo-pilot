"""Repository Profiler (§6.6, §14.2).

Deterministic static analysis (no Docker, no LLM) that extracts facts about a repo
and emits backing Evidence items (ADR-0010). Returns the Profile without a ``repo``
block — the caller adds repo url/commit. This slice covers Node projects (Express,
Vite); more ecosystems arrive in later slices.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from repo_pilot.evidence import EvidenceBuilder

# Python web frameworks -> a reasonable foreground start command (service shape).
_PY_SERVICE_FRAMEWORKS = {
    "fastapi": "uvicorn app:app --host 0.0.0.0 --port 8000",
    "flask": "flask run --host 0.0.0.0 --port 8000",
    "django": "python manage.py runserver 0.0.0.0:8000",
}
_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_.-]+):(?!=)", re.MULTILINE)

# framework dependency -> canonical framework name (§6.6)
_FRAMEWORK_DEPS = {
    "express": "express",
    "vite": "vite",
    "next": "nextjs",
    "react": "react",
}

# lockfile -> package manager, in detection priority order
_LOCKFILES = [
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
]


def _detect_package_manager(repo_dir: Path) -> tuple[str, str, bool]:
    for lockfile, manager in _LOCKFILES:
        if (repo_dir / lockfile).is_file():
            return manager, lockfile, True
    return "npm", "package.json", False


def _pkg_name(spec: str) -> str:
    """The bare package name from a dependency spec (``fastapi>=0.1`` -> ``fastapi``)."""
    return re.split(r"[<>=!~;\[ ]", spec.strip(), 1)[0].lower()


def _profile_python(
    repo_dir: Path, ev: EvidenceBuilder, languages, package_managers, frameworks,
    entrypoints, evidence_refs,
) -> None:
    pyproject = repo_dir / "pyproject.toml"
    requirements = repo_dir / "requirements.txt"
    if not (pyproject.is_file() or requirements.is_file()):
        return

    py: dict = {}
    if pyproject.is_file():
        try:
            py = tomllib.loads(pyproject.read_text())
        except tomllib.TOMLDecodeError:
            py = {}
    manifest = "pyproject.toml" if pyproject.is_file() else "requirements.txt"

    if "python" not in languages:
        languages.append("python")
        evidence_refs.setdefault("language:python", []).append(
            ev.add(file=manifest, kind="package_manager", excerpt=f"{manifest} present",
                   reason="Python project", confidence=0.8)
        )
    if "pip" not in package_managers:
        package_managers.append("pip")

    # [project.scripts] -> installable CLI(s)
    for name, target in py.get("project", {}).get("scripts", {}).items():
        ref = ev.add(file="pyproject.toml", kind="package_script",
                     excerpt=f"[project.scripts] {name} = {target!r}",
                     reason=f"pyproject declares CLI script {name!r}", confidence=0.8)
        entrypoints.append({"type": "binary", "file": "pyproject.toml", "key": name,
                            "command": name, "evidence_refs": [ref]})

    # dependency names from pyproject + requirements.txt
    dep_names: set[str] = {
        _pkg_name(d) for d in py.get("project", {}).get("dependencies", [])
    }
    if requirements.is_file():
        for line in requirements.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                dep_names.add(_pkg_name(line))

    # web framework -> service; emit an inferred start entrypoint
    for fw, start in _PY_SERVICE_FRAMEWORKS.items():
        if fw in dep_names:
            frameworks.append(fw)
            ref = ev.add(file=manifest, kind="manifest_dependency", excerpt=fw,
                         reason=f"{fw} dependency", confidence=0.8)
            evidence_refs.setdefault(f"framework:{fw}", []).append(ref)
            entrypoints.append({"type": "inferred", "file": manifest, "key": "start",
                                "command": start, "evidence_refs": [ref]})

    # a test suite with no CLI/service -> library
    has_cli = any(e.get("type") == "binary" for e in entrypoints)
    has_service = any(e.get("key") == "start" for e in entrypoints)
    tests_present = (
        "pytest" in dep_names
        or any(repo_dir.glob("test_*.py"))
        or any(repo_dir.glob("tests/**/*.py"))
        or (repo_dir / "tests").is_dir()
    )
    if tests_present and not has_cli and not has_service:
        ref = ev.add(file=manifest, kind="package_script", excerpt="tests present",
                     reason="test suite present", confidence=0.6)
        # bare command; the planner folds in the language-appropriate install
        entrypoints.append({"type": "inferred", "file": manifest, "key": "test",
                            "command": "pytest", "evidence_refs": [ref]})


def _profile_go(
    repo_dir: Path, ev: EvidenceBuilder, languages, package_managers, entrypoints, evidence_refs,
) -> None:
    go_mod = repo_dir / "go.mod"
    if not go_mod.is_file():
        return
    languages.append("go")
    package_managers.append("go")
    ref = ev.add(file="go.mod", kind="package_manager", excerpt="go.mod present",
                 reason="Go module", confidence=0.9)
    evidence_refs.setdefault("language:go", []).append(ref)

    # A main package that opens a listener is a service; otherwise a CLI.
    sources = " ".join(p.read_text(errors="replace") for p in repo_dir.glob("*.go"))
    is_service = "ListenAndServe" in sources or "net/http" in sources
    if is_service:
        entrypoints.append({"type": "inferred", "file": "go.mod", "key": "start",
                            "command": "go run .", "evidence_refs": [ref]})
    elif "package main" in sources:
        entrypoints.append({"type": "binary", "file": "go.mod", "key": "app",
                            "command": "go run .", "evidence_refs": [ref]})


def _profile_make(repo_dir: Path, ev: EvidenceBuilder, entrypoints) -> None:
    makefile = repo_dir / "Makefile"
    if not makefile.is_file():
        return
    targets = set(_MAKE_TARGET.findall(makefile.read_text()))
    # Only surface build/test targets when no richer entrypoint already exists.
    if any(e.get("key") in ("start", "test") or e.get("type") == "binary" for e in entrypoints):
        return
    # `run` is a batch job (runs to completion); build/test are their own shapes.
    for target in ("build", "test", "run"):
        if target in targets:
            ref = ev.add(file="Makefile", kind="ci_step", excerpt=f"{target}:",
                         reason=f"Makefile {target} target", confidence=0.6)
            entrypoints.append({"type": "inferred", "file": "Makefile", "key": target,
                                "command": f"make {target}", "evidence_refs": [ref]})


def profile(
    repo_dir: str | Path, builder: EvidenceBuilder | None = None
) -> tuple[dict, list[dict]]:
    repo_dir = Path(repo_dir)
    ev = builder if builder is not None else EvidenceBuilder()

    languages: list[str] = []
    package_managers: list[str] = []
    frameworks: list[str] = []
    entrypoints: list[dict] = []
    evidence_refs: dict[str, list[str]] = {}

    pkg_file = repo_dir / "package.json"
    if pkg_file.is_file():
        data = json.loads(pkg_file.read_text())
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}

        is_ts = "typescript" in deps or (repo_dir / "tsconfig.json").is_file()
        language = "typescript" if is_ts else "javascript"
        languages.append(language)
        evidence_refs[f"language:{language}"] = [
            ev.add(
                file="package.json",
                kind="package_manager",
                excerpt="package.json present",
                reason=f"Node project ({language})",
                confidence=0.8,
            )
        ]

        manager, lock_file, has_lock = _detect_package_manager(repo_dir)
        package_managers.append(manager)
        evidence_refs[f"package_manager:{manager}"] = [
            ev.add(
                file=lock_file,
                kind="package_manager",
                excerpt=lock_file,
                reason=f"{manager} project"
                + (" (lockfile)" if has_lock else " (default; no lockfile)"),
                confidence=0.9 if has_lock else 0.5,
            )
        ]

        for dep, framework in _FRAMEWORK_DEPS.items():
            if dep in deps:
                frameworks.append(framework)
                evidence_refs[f"framework:{framework}"] = [
                    ev.add(
                        file="package.json",
                        kind="manifest_dependency",
                        excerpt=f'"{dep}": "{deps[dep]}"',
                        reason=f"{framework} dependency",
                        confidence=0.8,
                    )
                ]

        # start/dev mark a service; test/build mark library/build shapes when no
        # service start is present (shape_detection decides — profiler only records).
        for key in ("dev", "start", "test", "build"):
            command = data.get("scripts", {}).get(key)
            if command:
                ref = ev.add(
                    file="package.json",
                    kind="package_script",
                    excerpt=f'"{key}": "{command}"',
                    reason=f"scripts.{key}",
                    confidence=0.8,
                )
                entrypoints.append(
                    {
                        "type": "script",
                        "file": "package.json",
                        "key": key,
                        "command": command,
                        "evidence_refs": [ref],
                    }
                )

        # `bin` marks an installable CLI. It may be a string (single bin named after
        # the package) or an object of {name: path}.
        bin_field = data.get("bin")
        bin_names: list[str] = []
        if isinstance(bin_field, str):
            name = data.get("name", "cli")
            bin_names = [name]
        elif isinstance(bin_field, dict):
            bin_names = list(bin_field.keys())
        for name in bin_names:
            ref = ev.add(
                file="package.json",
                kind="package_script",
                excerpt=f'"bin": {json.dumps(bin_field)}'[:200],
                reason=f"package declares CLI bin {name!r}",
                confidence=0.8,
            )
            entrypoints.append(
                {
                    "type": "binary",
                    "file": "package.json",
                    "key": name,
                    "command": name,
                    "evidence_refs": [ref],
                }
            )

    # Non-Node ecosystems (Task 9). Each is cheap, deterministic extraction; a
    # helper records language/package-manager/framework/entrypoints + evidence.
    _profile_python(repo_dir, ev, languages, package_managers, frameworks, entrypoints, evidence_refs)
    _profile_go(repo_dir, ev, languages, package_managers, entrypoints, evidence_refs)
    _profile_make(repo_dir, ev, entrypoints)

    prof = {
        "languages": languages,
        "frameworks": frameworks,
        "package_managers": package_managers,
        "entrypoints": entrypoints,
    }
    if evidence_refs:
        prof["evidence_refs"] = evidence_refs
    return prof, ev.items
