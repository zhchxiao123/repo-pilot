"""Coverage eval harness (#44).

Measures how often repo-pilot produces a *correct* verdict across a set of repos —
the metric behind the ≥90% goal (given 1000 repos, correctly output the startup
method for 900). "Correct" = the pipeline's verdict matches the case's expected
verdict, where a verdict is one of:

- ``verified``       — the run flow was sandbox-verified (a service came up, or a
                       non-service was exercised to a clean result)
- ``not-a-service``  — correctly judged not a runnable system (docs-only, ...)
- ``failed``         — a candidate was tried but did not verify
- ``deferred`` / ``no-candidate`` — nothing was run

The scoring/clustering core is pure and unit-tested; the runner drives the real
graph (Docker + LLM) and is used operationally. Failures are clustered by
expected->actual so you can see *how* coverage is missed and iterate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class EvalCase:
    name: str
    repo_url: str
    expected: str  # verified | not-a-service | failed | deferred | no-candidate
    commit: str | None = None


@dataclass(frozen=True)
class EvalResult:
    name: str
    expected: str
    actual: str
    detail: str = ""

    @property
    def correct(self) -> bool:
        return self.actual == self.expected


@dataclass(frozen=True)
class EvalReport:
    results: list[EvalResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def correct(self) -> int:
        return sum(1 for r in self.results if r.correct)

    @property
    def coverage(self) -> float:
        return self.correct / self.total if self.total else 0.0


def verdict_of(final: dict) -> str:
    """Reduce a graph final state to a single verdict category."""
    if final.get("verified"):
        return "verified"
    reason = final.get("deferred_reason")
    if isinstance(reason, str) and reason.startswith("not-a-service"):
        return "not-a-service"
    if final.get("runbook") is not None:
        return "failed"
    if reason:
        return "deferred"
    return "no-candidate"


def _hint(final: dict) -> str:
    """A short, human-useful reason for a non-verified outcome (for clustering)."""
    reason = final.get("deferred_reason")
    if isinstance(reason, str) and reason:
        return reason
    logs = final.get("last_logs")
    if isinstance(logs, str) and logs:
        return logs.strip().splitlines()[-1][:160]
    return ""


def evaluate(cases: list[EvalCase], run_fn: Callable[[EvalCase], dict]) -> EvalReport:
    """Run every case through ``run_fn`` (which returns a graph final state) and
    score it. A run_fn that raises is recorded as an ``error`` verdict rather than
    aborting the whole sweep."""
    results = []
    for case in cases:
        try:
            final = run_fn(case)
            results.append(EvalResult(case.name, case.expected, verdict_of(final), _hint(final)))
        except Exception as exc:  # a single case must not sink the eval
            results.append(EvalResult(case.name, case.expected, "error", str(exc)[:160]))
    return EvalReport(results)


def cluster_failures(report: EvalReport) -> dict[str, list[str]]:
    """Group incorrect cases by an ``expected->actual`` signature, so the dominant
    failure modes are visible at a glance."""
    clusters: dict[str, list[str]] = {}
    for r in report.results:
        if r.correct:
            continue
        clusters.setdefault(f"{r.expected}->{r.actual}", []).append(r.name)
    return dict(sorted(clusters.items(), key=lambda kv: len(kv[1]), reverse=True))


def format_report(report: EvalReport) -> str:
    lines = [
        "# repo-pilot coverage eval",
        "",
        f"- Coverage: {report.coverage:.1%} ({report.correct}/{report.total})",
        "",
        "## Cases",
        "",
    ]
    for r in report.results:
        mark = "OK " if r.correct else "XX "
        suffix = f" — {r.detail}" if (r.detail and not r.correct) else ""
        lines.append(f"- {mark}{r.name}: expected {r.expected}, got {r.actual}{suffix}")
    clusters = cluster_failures(report)
    if clusters:
        lines += ["", "## Failure clusters", ""]
        for sig, names in clusters.items():
            lines.append(f"- {sig} ({len(names)}): {', '.join(names)}")
    return "\n".join(lines) + "\n"


def load_manifest(path: str | Path) -> list[EvalCase]:
    """Load cases from a JSON manifest: [{name, repo_url, expected, commit?}, ...]."""
    data = json.loads(Path(path).read_text())
    return [
        EvalCase(
            name=item["name"],
            repo_url=item["repo_url"],
            expected=item["expected"],
            commit=item.get("commit"),
        )
        for item in data
    ]


def run_case(case: EvalCase, build_graph_fn: Callable[[], Any], workdir: Path) -> dict:
    """Run one case through a freshly built graph, isolating its artifacts."""
    from repo_pilot.graph import initial_state

    case_dir = workdir / case.name
    case_dir.mkdir(parents=True, exist_ok=True)
    graph = build_graph_fn()
    return graph.invoke(
        initial_state(
            repo_url=case.repo_url,
            commit=case.commit,
            repo_dir=str(case_dir / "repo"),
            report_path=str(case_dir / "report.md"),
            runbook_path=str(case_dir / "runbook.yaml"),
            profile_path=str(case_dir / "profile.json"),
            evidence_path=str(case_dir / "evidence.jsonl"),
        )
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    from repo_pilot.config import load_config
    from repo_pilot.executor import DockerSandboxExecutor
    from repo_pilot.graph import build_graph
    from repo_pilot.model_client import build_chat_model, build_model_client
    from repo_pilot.security import default_security

    parser = argparse.ArgumentParser(prog="repo-pilot-eval", description=__doc__)
    parser.add_argument("manifest", help="JSON manifest of eval cases")
    parser.add_argument("--out", help="write the markdown report here")
    parser.add_argument("--no-llm", action="store_true", help="deterministic path only")
    args = parser.parse_args(argv)

    cases = load_manifest(args.manifest)
    config = load_config()
    chat_model = model_client = None
    if not args.no_llm:
        try:
            model_client = build_model_client(config)
            chat_model = build_chat_model(config)
        except Exception as exc:  # missing provider package / config
            print(f"LLM disabled ({exc}); deterministic path only.")

    def _build():
        return build_graph(
            DockerSandboxExecutor(),
            security=default_security(),
            model_client=model_client,
            chat_model=chat_model,
            healthcheck_retries=60,
            poll_interval=2.0,
        )

    workdir = Path(tempfile.mkdtemp(prefix="repo-pilot-eval-"))
    report = evaluate(cases, lambda case: run_case(case, _build, workdir))
    text = format_report(report)
    print(text)
    if args.out:
        Path(args.out).write_text(text)
    # non-zero exit if coverage is below the goal, so CI can gate on it
    return 0 if report.coverage >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
