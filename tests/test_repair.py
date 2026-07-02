"""Repair Loop (ADR-0012): rule-first then LLM patching of a failed Runbook."""

import json

from repo_pilot.model_client import ReplayModelClient
from repo_pilot.repair import patch_fingerprint, propose_repair
from repo_pilot.schemas import validate_runbook

RUNBOOK = {
    "schema_version": "v1",
    "id": "c1",
    "status": "failed",
    "repo": {"url": "x", "commit": "y"},
    "runtime": {"image": "node:20-bookworm", "workdir": "/workspace/repo"},
    "steps": {"setup": [{"command": "pnpm install"}], "start": [{"command": "pnpm dev", "expected_ports": [3000]}]},
    "healthcheck": {"strategy": "http"},
}


def test_rule_inserts_corepack_for_pnpm_not_found():
    patched, desc, source = propose_repair(RUNBOOK, "pnpm: command not found", None)
    assert source == "rule"
    assert patched["steps"]["setup"][0]["command"] == "corepack enable"
    validate_runbook(patched)


def test_rule_adds_pip_install_for_module_not_found():
    py = {**RUNBOOK, "runtime": {"image": "python:3.11", "workdir": "/workspace/repo"},
          "steps": {"start": [{"command": "python app.py"}]}}
    patched, desc, source = propose_repair(py, "ModuleNotFoundError: No module named flask", None)
    assert source == "rule"
    assert any("pip install" in s["command"] for s in patched["steps"]["setup"])


def test_rule_provisions_postgres_on_db_connection_failure():
    py = {**RUNBOOK, "runtime": {"image": "python:3.11", "workdir": "/workspace/repo"},
          "steps": {"start": [{"command": "python app.py"}]}}
    patched, desc, source = propose_repair(
        py, "could not connect to server: Connection refused (postgres:5432)", None
    )
    assert source == "rule"
    assert patched["services"][0]["name"] == "postgres"
    assert patched["env"]["generated"]["DATABASE_URL"].startswith("postgresql://")
    validate_runbook(patched)


def test_llm_repair_when_no_rule_matches():
    client = ReplayModelClient([
        json.dumps({"image": "node:20-bookworm", "setup": ["npm ci"], "start": "npm run serve", "port": 8080})
    ])
    patched, desc, source = propose_repair(RUNBOOK, "some novel error", client)
    assert source == "llm"
    assert patched["steps"]["start"][0]["command"] == "npm run serve"
    assert patched["steps"]["start"][0]["expected_ports"] == [8080]
    validate_runbook(patched)


def test_returns_none_when_no_rule_and_no_client():
    assert propose_repair(RUNBOOK, "some novel error", None) is None


def test_fingerprint_changes_with_the_patch():
    a = patch_fingerprint(RUNBOOK)
    patched, _d, _s = propose_repair(RUNBOOK, "pnpm: command not found", None)
    assert patch_fingerprint(patched) != a
