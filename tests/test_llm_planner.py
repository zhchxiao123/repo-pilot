"""LLM-assisted planning: propose full Runbook candidates for stacks rules miss.

Uses ReplayModelClient — no live tokens. The proposals are schema-validated;
verification is still the sandbox's job (tested at the graph level).
"""

import json

from repo_pilot.llm_planner import gather_context, propose_runbooks
from repo_pilot.model_client import ReplayModelClient
from repo_pilot.schemas import validate_runbook

PROFILE = {"languages": ["python"], "frameworks": [], "package_managers": [], "repo": {"url": "x", "commit": "y"}}


def test_gather_context_includes_key_files(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask==3.0.3\n")
    (tmp_path / "app.py").write_text("print('hi')\n")
    ctx = gather_context(tmp_path)
    assert "requirements.txt" in ctx
    assert "flask==3.0.3" in ctx  # key-file snippet included
    assert "app.py" in ctx        # listing included


def test_proposes_schema_valid_candidate():
    client = ReplayModelClient([
        json.dumps([
            {
                "image": "python:3.11-bookworm",
                "setup": ["pip install -r requirements.txt"],
                "start": "python app.py",
                "port": 8000,
            }
        ])
    ])
    candidates, evidence = propose_runbooks(PROFILE, [], "files...", client)
    assert len(candidates) == 1
    rb = candidates[0]
    validate_runbook(rb)
    assert rb["runtime"]["image"] == "python:3.11-bookworm"
    assert rb["steps"]["start"][0]["command"] == "python app.py"
    assert rb["steps"]["start"][0]["expected_ports"] == [8000]
    assert rb["evidence_refs"] == [evidence[0]["id"]]
    assert evidence[0]["kind"] == "llm_inference"


def test_drops_unparseable_or_incomplete_output():
    assert propose_runbooks(PROFILE, [], "x", ReplayModelClient(["not json"])) == ([], [])
    # missing required 'start'
    bad = ReplayModelClient([json.dumps([{"image": "python:3.11"}])])
    assert propose_runbooks(PROFILE, [], "x", bad) == ([], [])
