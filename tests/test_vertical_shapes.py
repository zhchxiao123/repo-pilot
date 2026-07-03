"""End-to-end shape verification through the canonical pipeline (issues #52-#55).

Each test drives one runnable shape from a fixture through the full pipeline with
no Docker: profile -> detect -> plan -> verify -> outcome. It proves the shape is
planned with the *right ecosystem* (image + command) and verifies by being
exercised, mirroring test_vertical_cli.py.
"""

from __future__ import annotations

from repo_pilot.candidate_planning import plan_candidates
from repo_pilot.executor import FakeSandboxExecutor
from repo_pilot.outcome import OutcomeKind, outcome_from_verification
from repo_pilot.profiler import profile
from repo_pilot.run_shape import RunShape, normalize_plan
from repo_pilot.run_verifier import verify_run_plan
from repo_pilot.schemas import validate_profile
from repo_pilot.shape_detection import detect_shapes


def _profiled(fixture_repo, name):
    prof, ev = profile(fixture_repo(name))
    prof["repo"] = {"url": "u", "commit": "c"}
    validate_profile(prof)
    return prof, ev


# --- #52: Python service -------------------------------------------------------


def test_python_flask_service_planned_and_verified(fixture_repo, tmp_path):
    prof, ev = _profiled(fixture_repo, "flask-min")
    assert detect_shapes(prof, ev).primary.shape == "service"

    plan = plan_candidates(prof, ev).candidates[0]
    assert plan.shape == RunShape.SERVICE
    comp = plan.components[0]
    assert comp.image == "python:3.11"  # NOT node
    assert "pip install" in comp.command and "flask run" in comp.command
    assert comp.oracle.type == "http"

    executor = FakeSandboxExecutor(
        component_ports={comp.name: {comp.oracle.port: 49152}},
        states={comp.name: ("running", None, None)},
        responses={comp.oracle.path: 200},
    )
    v = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert v.verified is True
    assert outcome_from_verification(normalize_plan(plan), v).kind == OutcomeKind.VERIFIED


# --- #53: Go CLI ---------------------------------------------------------------


def test_go_cli_planned_with_go_toolchain_not_node(fixture_repo, tmp_path):
    prof, ev = _profiled(fixture_repo, "go-cli")
    assert detect_shapes(prof, ev).primary.shape == "cli"

    plan = plan_candidates(prof, ev).candidates[0]
    assert plan.shape == RunShape.CLI
    comp = plan.components[0]
    assert comp.image == "golang:1.22"  # NOT node:20 + npm
    assert comp.command == "go run ."
    assert "npm" not in comp.command

    executor = FakeSandboxExecutor(states={comp.name: ("exited", None, 0)})
    v = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert v.verified is True
    assert outcome_from_verification(normalize_plan(plan), v).kind == OutcomeKind.VERIFIED


# --- #54: Library --------------------------------------------------------------


def test_python_library_verified_by_running_tests(fixture_repo, tmp_path):
    prof, ev = _profiled(fixture_repo, "lib-min")
    assert detect_shapes(prof, ev).primary.shape == "library"

    plan = plan_candidates(prof, ev).candidates[0]
    assert plan.shape == RunShape.LIBRARY
    comp = plan.components[0]
    assert comp.image == "python:3.11"
    assert "pip install" in comp.command and comp.command.endswith("pytest")
    assert comp.oracle.type == "tests-pass"

    executor = FakeSandboxExecutor(states={comp.name: ("exited", None, 0)})
    v = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert v.verified is True
    assert outcome_from_verification(normalize_plan(plan), v).shape == "library"


# --- #55: Build ----------------------------------------------------------------


def test_makefile_build_verified_by_building(fixture_repo, tmp_path):
    prof, ev = _profiled(fixture_repo, "make-build")
    assert detect_shapes(prof, ev).primary.shape == "build"

    plan = plan_candidates(prof, ev).candidates[0]
    assert plan.shape == RunShape.BUILD
    comp = plan.components[0]
    assert comp.command == "make build"
    assert comp.oracle.type == "build-succeeds"

    executor = FakeSandboxExecutor(states={comp.name: ("exited", None, 0)})
    v = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert v.verified is True
    assert outcome_from_verification(normalize_plan(plan), v).kind == OutcomeKind.VERIFIED


# --- #55: Batch ----------------------------------------------------------------


def test_makefile_run_job_verified_by_exit_zero(fixture_repo, tmp_path):
    prof, ev = _profiled(fixture_repo, "make-batch")
    assert detect_shapes(prof, ev).primary.shape == "batch"

    plan = plan_candidates(prof, ev).candidates[0]
    assert plan.shape == RunShape.BATCH
    comp = plan.components[0]
    assert comp.command == "make run"
    assert comp.oracle.type == "exit-zero"

    executor = FakeSandboxExecutor(states={comp.name: ("exited", None, 0)})
    v = verify_run_plan(plan, executor, repo_dir=str(tmp_path))
    assert v.verified is True
    assert outcome_from_verification(normalize_plan(plan), v).kind == OutcomeKind.VERIFIED
