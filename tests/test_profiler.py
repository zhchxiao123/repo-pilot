"""Repository Profiler: deterministic static analysis into facts + evidence (§6.6).

Runs with no Docker and no LLM.
"""

from repo_pilot.profiler import profile


def _evidence_by_id(evidence):
    return {e["id"]: e for e in evidence}


def test_express_fixture_profile(fixture_repo):
    prof, evidence = profile(fixture_repo("express-min"))
    assert "javascript" in prof["languages"]
    assert "npm" in prof["package_managers"]
    assert "express" in prof["frameworks"]
    # a start entrypoint was found, and it cites real evidence
    starts = [e for e in prof["entrypoints"] if e["key"] == "start"]
    assert starts
    refs = starts[0]["evidence_refs"]
    assert refs and all(r in _evidence_by_id(evidence) for r in refs)


def test_vite_fixture_profile(fixture_repo):
    prof, _evidence = profile(fixture_repo("vite-min"))
    assert "vite" in prof["frameworks"]
    assert any(e["key"] == "dev" for e in prof["entrypoints"])


def test_profiler_emits_no_repo_block(fixture_repo):
    # repo{url,commit} is added by the caller (graph), not the profiler
    prof, _ = profile(fixture_repo("express-min"))
    assert "repo" not in prof


def test_scalar_conclusions_carry_resolving_evidence_refs(fixture_repo):
    prof, evidence = profile(fixture_repo("express-min"))
    ids = {e["id"] for e in evidence}
    refs = prof["evidence_refs"]
    # language / package manager / framework conclusions are all traceable
    assert "language:javascript" in refs
    assert "package_manager:npm" in refs
    assert "framework:express" in refs
    for conclusion_refs in refs.values():
        assert conclusion_refs and set(conclusion_refs) <= ids


def test_python_lib_profiles_as_library(fixture_repo):
    prof, evidence = profile(fixture_repo("lib-min"))
    assert "python" in prof["languages"] and "pip" in prof["package_managers"]
    # a test suite, no CLI/service -> an inferred test entrypoint
    assert any(e["key"] == "test" for e in prof["entrypoints"])
    assert not any(e["type"] == "binary" for e in prof["entrypoints"])


def test_flask_requirements_profiles_as_service(fixture_repo):
    prof, _ = profile(fixture_repo("flask-min"))
    assert "python" in prof["languages"]
    assert "flask" in prof["frameworks"]
    assert any(e["key"] == "start" for e in prof["entrypoints"])


def test_go_module_profiles_as_cli(fixture_repo):
    prof, _ = profile(fixture_repo("go-cli"))
    assert "go" in prof["languages"]
    # no net/http -> a CLI binary entrypoint, not a service
    assert any(e["type"] == "binary" for e in prof["entrypoints"])
    assert not any(e["key"] == "start" for e in prof["entrypoints"])


def test_makefile_build_target_is_captured(fixture_repo):
    prof, _ = profile(fixture_repo("make-build"))
    assert any(e["key"] == "build" for e in prof["entrypoints"])
