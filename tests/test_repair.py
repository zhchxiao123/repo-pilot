"""Repair Loop (ADR-0012): rule-first then LLM patching of a failed RunPlan."""

import json

from repo_pilot.model_client import ReplayModelClient
from repo_pilot.repair import patch_fingerprint, propose_repair
from repo_pilot.run_shape import Oracle, RunComponent, RunPlan, RunShape

REPO = {"url": "x", "commit": "y"}


def _service_plan(command: str, image: str = "node:20-bookworm") -> RunPlan:
    return RunPlan(
        id="c1", shape=RunShape.SERVICE, repo=REPO,
        components=[RunComponent(name="app", image=image, workdir="/workspace/repo",
                    command=command, ports=[3000],
                    oracle=Oracle(type="http", port=3000, path="/"))],
    )


def _primary(plan):
    return next(c for c in plan.components if c.command) if any(c.command for c in plan.components) else plan.components[0]


def test_rule_inserts_corepack_for_pnpm_not_found():
    proposal = propose_repair(_service_plan("pnpm dev"), "pnpm: command not found", None)
    assert proposal.source == "rule"
    assert _primary(proposal.plan).command.startswith("corepack enable && ")


def test_rule_adds_pip_install_for_module_not_found():
    plan = _service_plan("python app.py", image="python:3.11")
    proposal = propose_repair(plan, "ModuleNotFoundError: No module named flask", None)
    assert proposal.source == "rule"
    assert "pip install" in _primary(proposal.plan).command


def test_rule_provisions_postgres_on_db_connection_failure():
    plan = _service_plan("python app.py", image="python:3.11")
    proposal = propose_repair(
        plan, "could not connect to server: Connection refused (postgres:5432)", None
    )
    assert proposal.source == "rule"
    names = [c.name for c in proposal.plan.components]
    assert "postgres" in names
    assert proposal.plan.shape == RunShape.MULTI_COMPONENT_SERVICE
    assert _primary(proposal.plan).env["DATABASE_URL"].startswith("postgresql://")


def test_llm_repair_when_no_rule_matches():
    client = ReplayModelClient([
        json.dumps({"image": "node:20-bookworm", "setup": ["npm ci"], "start": "npm run serve", "port": 8080})
    ])
    proposal = propose_repair(_service_plan("npm run bad"), "some novel error", client)
    assert proposal.source == "llm"
    prim = _primary(proposal.plan)
    assert prim.command == "npm ci && npm run serve"  # setup folded into command
    assert prim.ports == [8080]
    assert prim.oracle.port == 8080  # http oracle re-pointed at the new port


def test_returns_none_when_no_rule_and_no_client():
    assert propose_repair(_service_plan("npm start"), "some novel error", None) is None


def test_fingerprint_changes_with_the_patch():
    plan = _service_plan("pnpm dev")
    a = patch_fingerprint(plan)
    proposal = propose_repair(plan, "pnpm: command not found", None)
    assert patch_fingerprint(proposal.plan) != a
