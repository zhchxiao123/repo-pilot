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
