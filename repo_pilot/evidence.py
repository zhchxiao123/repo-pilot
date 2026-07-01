"""Evidence Store (ADR-0010).

The canonical home for facts. Each fact is stored once with an ``ev_*`` id;
conclusions elsewhere reference it via ``evidence_refs``. ``EvidenceBuilder``
accumulates items conforming to ``schemas/evidence.schema.json``.
"""

from __future__ import annotations

import json
from pathlib import Path


class EvidenceBuilder:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self._n = 0

    def add(
        self,
        *,
        file: str,
        kind: str,
        excerpt: str,
        reason: str,
        confidence: float,
        line: int | None = None,
    ) -> str:
        self._n += 1
        ev_id = f"ev_{self._n:03d}"
        self.items.append(
            {
                "id": ev_id,
                "file": file,
                "line": line,
                "kind": kind,
                "excerpt": excerpt,
                "reason": reason,
                "confidence": confidence,
            }
        )
        return ev_id


def write_evidence(path: str | Path, items: list[dict]) -> None:
    """Persist evidence items as JSON Lines (evidence.jsonl)."""
    with open(path, "w") as fh:
        for item in items:
            fh.write(json.dumps(item) + "\n")
