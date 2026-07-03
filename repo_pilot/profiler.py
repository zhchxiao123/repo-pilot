"""Repository Profiler (§6.6, §14.2).

Deterministic static analysis (no Docker, no LLM) that extracts facts about a repo
and emits backing Evidence items (ADR-0010). Returns the Profile without a ``repo``
block — the caller adds repo url/commit. This slice covers Node projects (Express,
Vite); more ecosystems arrive in later slices.
"""

from __future__ import annotations

import json
from pathlib import Path

from repo_pilot.evidence import EvidenceBuilder

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
                    "type": "bin",
                    "file": "package.json",
                    "key": name,
                    "command": name,
                    "evidence_refs": [ref],
                }
            )

    prof = {
        "languages": languages,
        "frameworks": frameworks,
        "package_managers": package_managers,
        "entrypoints": entrypoints,
    }
    if evidence_refs:
        prof["evidence_refs"] = evidence_refs
    return prof, ev.items
