"""The macro-skeleton graph runs every phase in the fixed DAG order (ADR-0006)."""

from repo_pilot.graph import MACRO_PHASES, build_graph, initial_state


def test_graph_runs_all_phases_in_order_clones_and_reports(tmp_path, git_origin):
    origin, _first, second = git_origin
    repo_dir = tmp_path / "work" / "repo"
    report_path = tmp_path / "report.md"

    graph = build_graph()
    final = graph.invoke(
        initial_state(
            repo_url=str(origin),
            commit=None,
            repo_dir=str(repo_dir),
            report_path=str(report_path),
        )
    )

    # every phase ran, in the fixed order
    assert final["visited"] == MACRO_PHASES
    # the clone phase populated repo_ref from real git facts
    assert final["repo_ref"].commit == second
    assert final["repo_ref"].default_branch == "main"
    # the report phase wrote a report containing the repo facts
    report = report_path.read_text()
    assert str(origin) in report
    assert second in report


def test_macro_phases_are_the_documented_dag():
    assert MACRO_PHASES == [
        "clone",
        "profile",
        "plan",
        "verify",
        "discover",
        "test",
        "report",
    ]
