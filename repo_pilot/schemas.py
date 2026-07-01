"""Load and validate documents against the JSON Schemas in ``schemas/``.

The Evidence Store, Profile, and Runbook are the system's structured contracts
(ADR-0003, ADR-0010). Validators raise :class:`SchemaValidationError` on invalid
input and return ``None`` on success.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import jsonschema


class SchemaValidationError(ValueError):
    """Raised when a document does not conform to its JSON Schema."""


def _schemas_dir() -> Path:
    override = os.environ.get("REPO_PILOT_SCHEMAS_DIR")
    if override:
        return Path(override)
    # repo_pilot/schemas.py -> repo root -> schemas/
    return Path(__file__).resolve().parent.parent / "schemas"


@lru_cache(maxsize=None)
def _load_schema(name: str) -> dict:
    path = _schemas_dir() / f"{name}.schema.json"
    if not path.is_file():
        raise FileNotFoundError(f"schema not found: {path}")
    return json.loads(path.read_text())


def _validate(name: str, document: object) -> None:
    schema = _load_schema(name)
    try:
        jsonschema.validate(instance=document, schema=schema)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(f"{name}: {exc.message}") from exc


def validate_evidence(document: object) -> None:
    _validate("evidence", document)


def validate_profile(document: object) -> None:
    _validate("profile", document)


def validate_runbook(document: object) -> None:
    _validate("runbook", document)
