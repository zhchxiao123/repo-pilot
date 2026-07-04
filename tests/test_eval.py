"""The coverage eval harness scores verdicts and clusters failures (#44).

Pure core — driven with a fake run_fn, no Docker or LLM. The CLI tests drive
`repo-pilot eval` with a fake graph, so no Docker or LLM either.
"""

import json

import pytest

from repo_pilot.eval import (
    EvalCase,
    cluster_failures,
    evaluate,
    format_report,
    load_manifest,
    select_cases,
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


def test_evaluate_attaches_artifact_dirs_to_results():
    cases = [EvalCase("a", "u", "verified")]
    report = evaluate(cases, _fake_run({"a": {}}), case_dir=lambda c: f"runs/{c.name}")
    assert report.results[0].artifact_dir == "runs/a"


def test_failure_clusters_point_to_artifact_dirs():
    cases = [EvalCase("a", "u", "verified")]
    report = evaluate(cases, _fake_run({"a": {}}), case_dir=lambda c: f"runs/{c.name}")
    text = format_report(report)
    assert "verified->no_candidate (1): a (runs/a)" in text


def test_select_cases_limits_and_picks_named_case():
    cases = [EvalCase(n, "u", "verified") for n in ("a", "b", "c")]
    assert select_cases(cases) == cases
    assert [c.name for c in select_cases(cases, limit=2)] == ["a", "b"]
    assert [c.name for c in select_cases(cases, case="b")] == ["b"]
    with pytest.raises(ValueError, match="nope"):
        select_cases(cases, case="nope")


# --- eval/manifest.50.json: the pinned coverage manifest (plan Task 1.1) -----


def test_manifest_50_pins_fifty_canonical_cases():
    import re
    from pathlib import Path

    cases = load_manifest(Path(__file__).parent.parent / "eval" / "manifest.50.json")
    assert len(cases) == 50
    assert len({c.name for c in cases}) == 50, "case names must be unique"
    for c in cases:
        assert c.commit and re.fullmatch(r"[0-9a-f]{40}", c.commit), f"{c.name}: unpinned commit"
        assert c.repo_url.startswith("https://"), c.name
        kind = c.expected.split(":", 1)[0]
        assert kind in {"verified", "not_runnable", "deferred", "no_candidate", "failed"}, c.name
    # the manifest exercises the not-runnable path on purpose
    assert sum(1 for c in cases if c.expected.startswith("not_runnable")) >= 5
    # and the compose-first slice that Phase 2 will unlock
    assert sum(1 for c in cases if c.expected == "verified:multi_component_service") >= 10


# --- run_case: per-case artifact persistence (plan Task 1.3) -----------------


def test_run_case_isolates_artifacts_and_writes_final_state_summary(tmp_path):
    from repo_pilot.eval import run_case

    case = EvalCase("demo", "https://x/demo", "verified:service")
    fake = _FakeGraph({"https://x/demo": {"verified": True}})
    final = run_case(case, lambda: fake, tmp_path)

    case_dir = tmp_path / "demo"
    # graph artifact paths all point into the case dir, with canonical names
    assert final["profile_path"] == str(case_dir / "repo-profile.json")
    assert final["evidence_path"] == str(case_dir / "evidence.jsonl")
    assert final["report_path"] == str(case_dir / "report.md")
    assert final["compose_path"] == str(case_dir / "compose.generated.yaml")

    summary = json.loads((case_dir / "final-state-summary.json").read_text())
    assert summary["verdict"] == "verified:service"
    assert summary["expected"] == "verified:service"
    assert summary["correct"] is True


def test_run_case_writes_error_summary_and_reraises(tmp_path):
    from repo_pilot.eval import run_case

    case = EvalCase("boom", "https://x/boom", "verified")
    fake = _FakeGraph({"https://x/boom": RuntimeError("kaput")})
    with pytest.raises(RuntimeError, match="kaput"):
        run_case(case, lambda: fake, tmp_path)

    summary = json.loads((tmp_path / "boom" / "final-state-summary.json").read_text())
    assert summary["verdict"] == "error"
    assert "kaput" in summary["error"]


# --- `repo-pilot eval` CLI: drives the sweep through a fake graph -----------


class _FakeGraph:
    """Stands in for the compiled graph: returns a canned final state per repo_url."""

    def __init__(self, finals_by_url):
        self._finals = finals_by_url

    def invoke(self, state):
        final = self._finals[state["repo_url"]]
        if isinstance(final, Exception):
            raise final
        return {**state, **final}


def _cli_eval(tmp_path, monkeypatch, manifest_cases, finals_by_url, *extra_args):
    from click.testing import CliRunner

    from repo_pilot.cli import main

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_cases))
    monkeypatch.setattr(
        "repo_pilot.cli.build_graph", lambda *a, **k: _FakeGraph(finals_by_url)
    )
    workdir = tmp_path / "eval-runs"
    return (
        CliRunner().invoke(
            main,
            ["eval", str(manifest), "--workdir", str(workdir), "--no-llm", *extra_args],
        ),
        workdir,
    )


def test_eval_cli_reports_coverage_and_passes_threshold(tmp_path, monkeypatch):
    result, workdir = _cli_eval(
        tmp_path,
        monkeypatch,
        [
            {"name": "ok", "repo_url": "https://x/ok", "expected": "verified:service"},
            {"name": "miss", "repo_url": "https://x/miss", "expected": "verified:service"},
        ],
        {"https://x/ok": {"verified": True}, "https://x/miss": {}},
        "--threshold", "0.5",
    )
    assert result.exit_code == 0, result.output
    assert "Overall coverage: 50.0% (1/2)" in result.output
    # each case ran isolated under a per-sweep timestamped run dir
    runs = [p for p in workdir.iterdir() if p.is_dir()]
    assert len(runs) == 1
    run_dir = runs[0]
    assert (run_dir / "ok").is_dir() and (run_dir / "miss").is_dir()
    # the sweep report is persisted beside the case dirs and names them
    report_md = (run_dir / "eval-report.md").read_text()
    assert "Overall coverage: 50.0% (1/2)" in report_md
    assert str(run_dir / "miss") in report_md  # failure clusters point at artifacts
    assert json.loads((run_dir / "ok" / "final-state-summary.json").read_text())["correct"]


def test_eval_cli_exits_nonzero_below_threshold(tmp_path, monkeypatch):
    result, _ = _cli_eval(
        tmp_path,
        monkeypatch,
        [{"name": "miss", "repo_url": "https://x/miss", "expected": "verified:service"}],
        {"https://x/miss": {}},
        "--threshold", "0.5",
    )
    assert result.exit_code == 1
    assert "Overall coverage: 0.0%" in result.output


def test_eval_cli_case_crash_records_error_and_continues(tmp_path, monkeypatch):
    result, _ = _cli_eval(
        tmp_path,
        monkeypatch,
        [
            {"name": "boom", "repo_url": "https://x/boom", "expected": "verified:service"},
            {"name": "ok", "repo_url": "https://x/ok", "expected": "verified:service"},
        ],
        {"https://x/boom": RuntimeError("clone failed"), "https://x/ok": {"verified": True}},
        "--threshold", "0.5",
    )
    assert result.exit_code == 0, result.output
    assert "boom: expected verified:service, got error" in result.output
    assert "OK ok" in result.output


def test_eval_cli_limit_and_case_select_subset(tmp_path, monkeypatch):
    cases = [
        {"name": "a", "repo_url": "https://x/a", "expected": "verified:service"},
        {"name": "b", "repo_url": "https://x/b", "expected": "verified:service"},
    ]
    finals = {"https://x/a": {"verified": True}, "https://x/b": {"verified": True}}

    result, _ = _cli_eval(tmp_path, monkeypatch, cases, finals, "--limit", "1")
    assert result.exit_code == 0, result.output
    assert "(1/1)" in result.output and "b:" not in result.output

    result, _ = _cli_eval(tmp_path, monkeypatch, cases, finals, "--case", "b")
    assert result.exit_code == 0, result.output
    assert "(1/1)" in result.output and "a:" not in result.output


def test_eval_cli_rejects_unknown_case_name(tmp_path, monkeypatch):
    result, _ = _cli_eval(
        tmp_path,
        monkeypatch,
        [{"name": "a", "repo_url": "https://x/a", "expected": "verified"}],
        {"https://x/a": {"verified": True}},
        "--case", "nope",
    )
    assert result.exit_code != 0
    assert "nope" in result.output


def test_report_shows_per_shape_coverage():
    cases = [EvalCase("svc", "u", "verified:service"), EvalCase("cli", "u", "verified:cli")]
    finals = {
        "svc": {"verified": True, "classification": "service"},
        "cli": {"verified": True, "classification": "cli"},
    }
    text = format_report(evaluate(cases, _fake_run(finals)))
    assert "Service coverage" in text
    assert "Cli coverage" in text
