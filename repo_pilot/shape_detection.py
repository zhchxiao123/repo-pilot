"""Deterministic run-shape detection (Task 5).

Turns a ``Profile`` (+ ``Evidence``) into ranked ``ShapeHint``s — the *hypotheses*
about how a repo runs, not final truth. The sandbox still adjudicates any plan
built from these (ADR-0004); the LLM planner is only consulted when detection is
weak. This module is pure: it consumes the profile the profiler produced (which
owns all filesystem I/O) and never touches the repo itself.

Rules (highest-priority hint first):

- a ``start``/``dev`` script                 -> service (stronger with a web framework)
- a ``bin`` entrypoint                        -> cli
- a ``test`` script and no service start      -> library
- a ``build`` script and no service start     -> build
- a compose service in evidence               -> multi_component_service
- no runnable evidence at all                 -> docs
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repo_pilot.run_shape import RunShape

# Frameworks whose presence strengthens a service classification.
_WEB_FRAMEWORKS = frozenset(
    {"express", "vite", "nextjs", "react", "koa", "nest", "hapi",
     "fastapi", "flask", "django", "starlette"}
)


@dataclass(frozen=True)
class ShapeHint:
    """One ranked hypothesis about a repo's runnable shape."""

    shape: RunShape
    confidence: float
    evidence_refs: list[str] = field(default_factory=list)
    reason: str = ""
    commands: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ShapeHints:
    """Ranked shape hypotheses; ``primary`` is the highest-confidence one."""

    hints: list[ShapeHint] = field(default_factory=list)

    @property
    def primary(self) -> ShapeHint:
        if self.hints:
            return self.hints[0]
        return ShapeHint(RunShape.DOCS, 0.2, [], "no runnable evidence found", [])


def _entry_refs(entry: dict) -> list[str]:
    return list(entry.get("evidence_refs", []))


def detect_shapes(profile: dict, evidence: list[dict]) -> ShapeHints:
    entrypoints = profile.get("entrypoints", [])
    by_key: dict[str, dict] = {}
    for e in entrypoints:
        by_key.setdefault(e.get("key"), e)
    scripts = {e["key"]: e for e in entrypoints if e.get("type") == "script"}
    bins = [e for e in entrypoints if e.get("type") == "binary"]

    frameworks = set(profile.get("frameworks", []))
    has_web = bool(frameworks & _WEB_FRAMEWORKS)

    hints: list[ShapeHint] = []

    start = scripts.get("start") or scripts.get("dev")
    if start:
        hints.append(
            ShapeHint(
                RunShape.SERVICE,
                confidence=0.85 if has_web else 0.6,
                evidence_refs=_entry_refs(start),
                reason=(
                    f"{start['key']} script"
                    + (f" with web framework {sorted(frameworks & _WEB_FRAMEWORKS)}" if has_web else "")
                ),
                commands=[start["command"]],
            )
        )

    if bins:
        hints.append(
            ShapeHint(
                RunShape.CLI,
                confidence=0.7,
                evidence_refs=[r for b in bins for r in _entry_refs(b)],
                reason=f"declares CLI bin(s): {[b['key'] for b in bins]}",
                commands=[b["command"] for b in bins],
            )
        )

    if "test" in scripts and not start:
        hints.append(
            ShapeHint(
                RunShape.LIBRARY,
                confidence=0.7,
                evidence_refs=_entry_refs(scripts["test"]),
                reason="test script and no service start",
                commands=[scripts["test"]["command"]],
            )
        )

    if "build" in scripts and not start:
        hints.append(
            ShapeHint(
                RunShape.BUILD,
                confidence=0.6,
                evidence_refs=_entry_refs(scripts["build"]),
                reason="build script and no service start",
                commands=[scripts["build"]["command"]],
            )
        )

    if any(e.get("kind") == "compose_service" for e in evidence):
        hints.append(
            ShapeHint(
                RunShape.MULTI_COMPONENT_SERVICE,
                confidence=0.5,
                evidence_refs=[e["id"] for e in evidence if e.get("kind") == "compose_service" and "id" in e],
                reason="compose services present",
                commands=[],
            )
        )

    hints.sort(key=lambda h: h.confidence, reverse=True)
    return ShapeHints(hints)
