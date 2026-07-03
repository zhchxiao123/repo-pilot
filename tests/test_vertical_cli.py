"""Task 6.5: prove a non-service repo verifies end to end through the canonical
pipeline (profile -> detect -> plan -> verify -> outcome), with no Docker.

This is the midpoint milestone: it exercises every canonical module together on a
python-cli fixture, so the earlier plumbing tasks add up to a real capability.
"""

from __future__ import annotations

from repo_pilot.candidate_planning import plan_candidates
from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.outcome import OutcomeKind, outcome_from_verification
from repo_pilot.profiler import profile
from repo_pilot.run_shape import RunShape, normalize_plan
from repo_pilot.run_verifier import verify_run_plan
from repo_pilot.schemas import validate_evidence, validate_profile
from repo_pilot.shape_detection import detect_shapes


def test_python_cli_verifies_by_being_exercised(fixture_repo, tmp_path):
    prof, evidence = profile(fixture_repo("python-cli"))
    prof["repo"] = {"url": "u", "commit": "c"}

    # profiler output is schema-valid (guards the entrypoint `type` regression)
    validate_profile(prof)
    for item in evidence:
        validate_evidence(item)

    # detection: a [project.scripts] entry is a CLI
    assert detect_shapes(prof, evidence).primary.shape == "cli"

    # planning: canonical CLI RunPlan that installs then runs the command
    plan = plan_candidates(prof, evidence).candidates[0]
    assert plan.shape == RunShape.CLI
    assert "pip install -e ." in plan.components[0].command
    assert plan.components[0].command.endswith("sample")

    # verify: the command runs to a clean exit (functional-smoke oracle)
    executor = FakeSandboxExecutor(states={plan.components[0].name: ("exited", None, 0)})
    v = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert v.verified is True

    # outcome: a verified CLI is VERIFIED (not "not a service")
    outcome = outcome_from_verification(normalize_plan(plan), v)
    assert outcome.kind == OutcomeKind.VERIFIED
    assert outcome.shape == "cli"
