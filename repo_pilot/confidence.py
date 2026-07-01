"""Confidence model (§7.1, ADR-0011, docs/confidence-model.md).

Deterministic (Tier-A) scoring: noisy-OR over the *distinct* evidence kinds
backing a candidate, times a conflict discount. Reliabilities and the discount are
tunable hyperparameters (calibrated on an eval set), not sacred.
"""

from __future__ import annotations

from collections.abc import Iterable

# Per-kind reliability r(kind): "how likely this source alone implies the correct
# run method." Distinct sources drive independence; same-kind items count once.
RELIABILITY: dict[str, float] = {
    "ci_step": 0.85,
    "ci_service": 0.85,
    "compose_service": 0.80,
    "dockerfile": 0.80,
    "devcontainer": 0.80,
    "readme_command": 0.70,
    "readme_env": 0.70,
    "package_script": 0.65,
    "package_manager": 0.65,
    "manifest_dependency": 0.60,
    "runtime_version": 0.60,
    "entrypoint_inference": 0.45,
    "port_inference": 0.45,
    "llm_inference": 0.30,
}

DEFAULT_RELIABILITY = 0.5
CONFLICT_DISCOUNT = 0.5


def reliability(kind: str) -> float:
    return RELIABILITY.get(kind, DEFAULT_RELIABILITY)


def confidence(kinds: Iterable[str], conflict: str | None = None) -> float:
    """Aggregate confidence from the distinct evidence kinds backing a candidate."""
    distinct = set(kinds)
    if not distinct:
        return 0.0

    support = 1.0
    for kind in distinct:
        support *= 1.0 - reliability(kind)
    score = 1.0 - support

    if conflict is not None:
        score *= 1.0 - CONFLICT_DISCOUNT * reliability(conflict)

    return max(0.0, min(1.0, score))
