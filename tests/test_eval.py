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
    assert verdict_of({"verified": True}) == "verified"
    assert verdict_of({"deferred_reason": "not-a-service:cli"}) == "not-a-service"
    assert verdict_of({"runbook": {"id": "x"}, "verified": False}) == "failed"
    assert verdict_of({"deferred_reason": "needs-compose"}) == "deferred"
    assert verdict_of({}) == "no-candidate"


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
        "c": {},                                    # verified->no-candidate
    }
    clusters = cluster_failures(evaluate(cases, _fake_run(finals)))
    assert clusters["verified->failed"] == ["a", "b"]
    assert clusters["verified->no-candidate"] == ["c"]
    # the dominant cluster sorts first
    assert list(clusters)[0] == "verified->failed"


def test_format_report_shows_coverage_and_clusters():
    cases = [EvalCase("a", "u", "verified"), EvalCase("b", "u", "verified")]
    finals = {"a": {"verified": True}, "b": {"runbook": {}, "verified": False}}
    text = format_report(evaluate(cases, _fake_run(finals)))
    assert "Coverage: 50.0% (1/2)" in text
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
