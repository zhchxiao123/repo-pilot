"""Runbook Planner: build evidence-backed candidates ranked by confidence (§7, §14.4)."""

import pytest

from repo_pilot.planner import plan
from repo_pilot.profiler import profile
from repo_pilot.schemas import validate_runbook


def _profiled(fixture_repo, name):
    prof, evidence = profile(fixture_repo(name))
    prof["repo"] = {"url": "https://x/y", "commit": "abc123"}
    return prof, evidence


def test_plans_express_start_candidate(fixture_repo):
    prof, evidence = _profiled(fixture_repo, "express-min")
    result = plan(prof, evidence)
    assert result.candidates
    best = result.candidates[0]
    assert best["status"] == "candidate"
    assert best["steps"]["setup"][0]["command"] == "npm install"
    assert best["steps"]["start"][0]["command"] == "npm start"
    assert best["confidence"] > 0
    assert best["evidence_refs"]  # chosen command traces to evidence


def test_plans_vite_dev_candidate(fixture_repo):
    prof, evidence = _profiled(fixture_repo, "vite-min")
    best = plan(prof, evidence).candidates[0]
    assert best["steps"]["start"][0]["command"] == "npm run dev"
    assert 5173 in best["steps"]["start"][0]["expected_ports"]


def test_candidate_validates_against_runbook_schema(fixture_repo):
    prof, evidence = _profiled(fixture_repo, "express-min")
    validate_runbook(plan(prof, evidence).candidates[0])


def test_candidates_ranked_by_confidence_desc(fixture_repo):
    prof, evidence = _profiled(fixture_repo, "express-min")
    scores = [c["confidence"] for c in plan(prof, evidence).candidates]
    assert scores == sorted(scores, reverse=True)


def test_express_candidate_confidence_matches_formula(fixture_repo):
    prof, evidence = _profiled(fixture_repo, "express-min")
    # package_script (0.65) + package_manager (0.65): 1 - (0.35 * 0.35) = 0.8775
    assert plan(prof, evidence).candidates[0]["confidence"] == pytest.approx(0.8775)


def test_readme_corroboration_raises_confidence(fixture_repo):
    prof, evidence = _profiled(fixture_repo, "express-min")
    base = plan(prof, evidence).candidates[0]["confidence"]
    corroborated = evidence + [
        {
            "id": "ev_readme",
            "file": "README.md",
            "line": None,
            "kind": "readme_command",
            "excerpt": "npm start",
            "reason": "README code block",
            "confidence": 0.6,
        }
    ]
    boosted = plan(prof, corroborated).candidates[0]
    assert boosted["confidence"] > base
    assert "ev_readme" in boosted["evidence_refs"]


def test_compose_only_repo_is_deferred():
    prof = {
        "repo": {"url": "x", "commit": "y"},
        "languages": [],
        "frameworks": [],
        "package_managers": [],
        "entrypoints": [],
    }
    evidence = [
        {
            "id": "ev_001",
            "file": "docker-compose.yml",
            "line": None,
            "kind": "compose_service",
            "excerpt": "services: ...",
            "reason": "compose file present",
            "confidence": 0.8,
        }
    ]
    result = plan(prof, evidence)
    assert not result.candidates
    assert result.deferred_reason == "needs-compose"
