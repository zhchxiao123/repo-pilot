"""Signal extractors emit Evidence from CI, README, Dockerfile, and compose (§6)."""

from repo_pilot.evidence import EvidenceBuilder
from repo_pilot.extractors import extract_signals


def _kinds(builder):
    return {e["kind"] for e in builder.items}


def test_extracts_readme_commands(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Setup\n\n```sh\nnpm install\nnpm run dev\n```\n\nsome prose\n"
    )
    builder = EvidenceBuilder()
    extract_signals(tmp_path, builder)
    cmds = [e["excerpt"] for e in builder.items if e["kind"] == "readme_command"]
    assert "npm install" in cmds
    assert "npm run dev" in cmds


def test_extracts_ci_run_steps(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "jobs:\n  build:\n    steps:\n      - run: npm ci\n      - run: npm test\n"
    )
    builder = EvidenceBuilder()
    extract_signals(tmp_path, builder)
    ci = [e["excerpt"] for e in builder.items if e["kind"] == "ci_step"]
    assert "npm ci" in ci and "npm test" in ci


def test_extracts_dockerfile_and_compose_presence(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM node:20\n")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    builder = EvidenceBuilder()
    extract_signals(tmp_path, builder)
    assert "dockerfile" in _kinds(builder)
    assert "compose_service" in _kinds(builder)


def test_no_signals_is_no_evidence(tmp_path):
    builder = EvidenceBuilder()
    extract_signals(tmp_path, builder)
    assert builder.items == []
