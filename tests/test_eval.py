"""The coverage eval harness scores verdicts and clusters failures (#44).

Pure core — driven with a fake run_fn, no Docker or LLM.
"""

import json

from repo_pilot.eval import (
    EvalCase,
    cluster_failures,
    evaluate,
    format_report,
    load_manifest,
    verdict_of,
)


def test_verdict_of_maps_each_terminal_state():
    # Canonical compound vocabulary: verified/not_runnable carry the shape.
    assert verdict_of({"verified": True}) == "verified:service"
    assert verdict_of({"verified": True, "classification": "cli"}) == "verified:cli"
    assert verdict_of({"deferred_reason": "not-a-service:cli"}) == "not_runnable:cli"
    assert verdict_of({"runbook": {"id": "x"}, "verified": False}) == "failed"
    assert verdict_of({"deferred_reason": "needs-compose"}) == "deferred"
    assert verdict_of({}) == "no_candidate"


def test_matches_is_hierarchical_and_aliases_legacy_tokens():
    from repo_pilot.eval import matches

    assert matches("verified", "verified:cli")  # coarse subsumes finer
    assert matches("not-a-service", "not_runnable:docs")  # legacy alias
    assert not matches("verified:service", "verified:cli")  # finer is specific
    assert not matches("verified", "failed")


def _fake_run(mapping):
    return lambda case: mapping[case.name]


def test_evaluate_computes_coverage():
    cases = [
        EvalCase("a", "u", "verified"),
        EvalCase("b", "u", "not-a-service"),
        EvalCase("c", "u", "verified"),
    ]
    finals = {
        "a": {"verified": True},                              # correct
        "b": {"deferred_reason": "not-a-service:docs"},       # correct
        "c": {"runbook": {"id": "c"}, "verified": False, "last_logs": "boom\ntrace"},  # wrong
    }
    report = evaluate(cases, _fake_run(finals))
    assert report.total == 3 and report.correct == 2
    assert abs(report.coverage - 2 / 3) < 1e-9
    c = next(r for r in report.results if r.name == "c")
    assert c.correct is False and c.actual == "failed" and "trace" in c.detail


def test_evaluate_records_errors_without_aborting():
    def boom(case):
        raise RuntimeError("clone failed")

    report = evaluate([EvalCase("x", "u", "verified")], boom)
    assert report.results[0].actual == "error" and "clone failed" in report.results[0].detail


def test_cluster_failures_groups_by_expected_to_actual():
    cases = [EvalCase(n, "u", "verified") for n in ("a", "b", "c")]
    finals = {
        "a": {"runbook": {}, "verified": False},   # verified->failed
        "b": {"runbook": {}, "verified": False},   # verified->failed
        "c": {},                                    # verified->no_candidate
    }
    clusters = cluster_failures(evaluate(cases, _fake_run(finals)))
    assert clusters["verified->failed"] == ["a", "b"]
    assert clusters["verified->no_candidate"] == ["c"]
    # the dominant cluster sorts first
    assert list(clusters)[0] == "verified->failed"


def test_format_report_shows_coverage_and_clusters():
    cases = [EvalCase("a", "u", "verified"), EvalCase("b", "u", "verified")]
    finals = {"a": {"verified": True}, "b": {"runbook": {}, "verified": False}}
    text = format_report(evaluate(cases, _fake_run(finals)))
    assert "Overall coverage: 50.0% (1/2)" in text
    assert "Failure clusters" in text and "verified->failed" in text


def test_load_manifest(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps([
        {"name": "repo1", "repo_url": "https://x/y", "expected": "verified", "commit": "abc"},
        {"name": "repo2", "repo_url": "https://x/z", "expected": "not-a-service"},
    ]))
    cases = load_manifest(path)
    assert cases[0] == EvalCase("repo1", "https://x/y", "verified", "abc")
    assert cases[1].commit is None


def test_coverage_by_shape_counts_cli_separately_from_service():
    from repo_pilot.eval import coverage_by_shape

    cases = [
        EvalCase("svc", "u", "verified:service"),
        EvalCase("cli", "u", "verified:cli"),
        EvalCase("lib", "u", "verified:library"),
    ]
    finals = {
        "svc": {"verified": True, "classification": "service"},   # correct
        "cli": {"verified": True, "classification": "cli"},       # correct
        "lib": {"runbook": {"id": "lib"}, "verified": False},     # wrong (failed)
    }
    by_shape = coverage_by_shape(evaluate(cases, _fake_run(finals)))
    assert by_shape["service"] == (1, 1)
    assert by_shape["cli"] == (1, 1)
    assert by_shape["library"] == (0, 1)


def test_docs_not_runnable_is_not_scored_as_failed():
    cases = [EvalCase("docs", "u", "not_runnable:docs")]
    finals = {"docs": {"classification": "docs", "deferred_reason": "not-a-service:docs"}}
    report = evaluate(cases, _fake_run(finals))
    assert report.correct == 1  # matched, not counted as a failure


def test_failure_clusters_include_shape():
    cases = [EvalCase("a", "u", "verified:service")]
    finals = {"a": {"runbook": {"id": "a"}, "verified": False}}  # -> failed
    clusters = cluster_failures(evaluate(cases, _fake_run(finals)))
    assert "verified:service->failed" in clusters


def test_report_shows_per_shape_coverage():
    cases = [EvalCase("svc", "u", "verified:service"), EvalCase("cli", "u", "verified:cli")]
    finals = {
        "svc": {"verified": True, "classification": "service"},
        "cli": {"verified": True, "classification": "cli"},
    }
    text = format_report(evaluate(cases, _fake_run(finals)))
    assert "Service coverage" in text
    assert "Cli coverage" in text
