"""Schema validators load schemas/*.json and accept valid / reject invalid docs.

Uses the JSON Schemas that back the Evidence Store, Profile, and Runbook
(ADR-0003, ADR-0010).
"""

import pytest

from repo_pilot.schemas import (
    SchemaValidationError,
    validate_evidence,
    validate_profile,
    validate_runbook,
)

VALID_EVIDENCE = {
    "id": "ev_001",
    "file": "package.json",
    "kind": "package_script",
    "excerpt": '"dev": "vite --host 0.0.0.0"',
    "reason": "scripts.dev exists",
    "confidence": 0.8,
}

VALID_PROFILE = {
    "repo": {"url": "https://github.com/org/repo", "commit": "abc123"},
    "languages": ["typescript"],
    "package_managers": ["pnpm"],
}

VALID_RUNBOOK = {
    "schema_version": "v1",
    "id": "node_pnpm_dev",
    "status": "candidate",
    "repo": {"url": "https://github.com/org/repo", "commit": "abc123"},
    "runtime": {"image": "node:20-bookworm", "workdir": "/workspace/repo"},
    "steps": {"start": [{"command": "pnpm dev --host 0.0.0.0"}]},
    "healthcheck": {"strategy": "http"},
}


def test_valid_evidence_passes():
    validate_evidence(VALID_EVIDENCE)


def test_invalid_evidence_rejected():
    bad = {**VALID_EVIDENCE}
    del bad["confidence"]  # required field missing
    with pytest.raises(SchemaValidationError):
        validate_evidence(bad)


def test_valid_profile_passes():
    validate_profile(VALID_PROFILE)


def test_invalid_profile_rejected():
    bad = {**VALID_PROFILE}
    del bad["package_managers"]  # required field missing
    with pytest.raises(SchemaValidationError):
        validate_profile(bad)


def test_valid_runbook_passes():
    validate_runbook(VALID_RUNBOOK)


def test_invalid_runbook_rejected():
    bad = {**VALID_RUNBOOK, "status": "not-a-status"}  # enum violation
    with pytest.raises(SchemaValidationError):
        validate_runbook(bad)


def test_runbook_requires_a_start_step():
    bad = {**VALID_RUNBOOK, "steps": {"start": []}}  # minItems 1
    with pytest.raises(SchemaValidationError):
        validate_runbook(bad)
