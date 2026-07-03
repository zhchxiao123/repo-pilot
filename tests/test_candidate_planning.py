"""Tests for canonical candidate planning (Task 6).

plan_candidates returns canonical RunPlans (not v1 dicts). Setup is folded into
the component command (the convention used across the canonical verifier tests),
so a Node service component installs and starts in one foreground command.
"""

from __future__ import annotations

from repo_pilot.candidate_planning import plan_candidates
from repo_pilot.run_shape import RunShape, normalize_plan


def _node_profile():
    return {
        "repo": {"url": "u", "commit": "c"},
        "languages": ["javascript"],
        "frameworks": ["express"],
        "package_managers": ["npm"],
        "entrypoints": [
            {"type": "script", "key": "start", "command": "node index.js",
             "evidence_refs": ["ev_1"]},
        ],
        "evidence_refs": {"package_manager:npm": ["ev_2"]},
    }


def test_node_start_script_becomes_service_run_plan():
    result = plan_candidates(_node_profile(), evidence=[])
    best = result.candidates[0]
    assert best.shape == RunShape.SERVICE
    # setup folded into the command: installs then starts
    assert best.components[0].command.endswith("npm start")
    assert "npm install" in best.components[0].command
    assert result.classification == "service"


def test_service_plan_is_normalizable():
    best = plan_candidates(_node_profile(), evidence=[]).candidates[0]
    normalize_plan(best)  # valid shape/oracle/image -> must not raise


def test_service_plan_carries_repo_and_evidence_refs():
    best = plan_candidates(_node_profile(), evidence=[]).candidates[0]
    assert best.repo == {"url": "u", "commit": "c"}
    assert "ev_1" in best.evidence_refs and "ev_2" in best.evidence_refs


def test_compose_only_repo_defers():
    profile = {"repo": {"url": "u", "commit": "c"}, "entrypoints": []}
    evidence = [{"id": "e", "kind": "compose_service"}]
    result = plan_candidates(profile, evidence)
    assert not result.candidates
    assert result.deferred_reason == "needs-compose"


def test_no_runnable_evidence_yields_no_candidates():
    result = plan_candidates({"repo": {"url": "u", "commit": "c"}, "entrypoints": []}, [])
    assert not result.candidates and result.deferred_reason is None
