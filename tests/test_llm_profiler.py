"""LLM-assisted profiling: enrich a thin Profile with structured signals."""

import json

from repo_pilot.llm_profiler import enrich_profile
from repo_pilot.model_client import ReplayModelClient
from repo_pilot.schemas import validate_profile

THIN = {"languages": [], "frameworks": [], "package_managers": [], "repo": {"url": "x", "commit": "y"}}


def test_enrich_adds_structured_signals():
    client = ReplayModelClient([
        json.dumps({
            "languages": ["python"],
            "frameworks": ["flask"],
            "services": ["postgres"],
            "env_required": ["DATABASE_URL"],
            "ports": [8000],
        })
    ])
    enriched, evidence = enrich_profile(THIN, "files...", client)
    assert "python" in enriched["languages"]
    assert "flask" in enriched["frameworks"]
    assert enriched["services"] == ["postgres"]
    assert enriched["env_vars"]["required"] == ["DATABASE_URL"]
    assert enriched["ports"] == [8000]
    assert evidence[0]["kind"] == "llm_inference"
    assert enriched["evidence_refs"]["llm_profile"] == [evidence[0]["id"]]
    validate_profile(enriched)


def test_enrich_is_noop_on_bad_or_empty_output():
    assert enrich_profile(THIN, "x", ReplayModelClient(["not json"])) == (THIN, [])
    empty = ReplayModelClient([json.dumps({"languages": [], "frameworks": []})])
    assert enrich_profile(THIN, "x", empty) == (THIN, [])
