"""The plan agent explores via tools then submits a classification + run plans."""

from langchain_core.messages import AIMessage

from repo_pilot.explore_tools import RepoTools
from repo_pilot.plan_agent import explore_and_plan
from repo_pilot.run_shape import RunShape, normalize_plan

REPO = {"url": "https://x/y", "commit": "abc"}


class FakeChatModel:
    """Scripted tool-calling model: each turn is a list of tool_call dicts."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.invocations = 0

    def bind_tools(self, tools):
        self._tools = tools
        return self

    def invoke(self, messages):
        self.invocations += 1
        calls = self._turns.pop(0) if self._turns else []
        return AIMessage(content="", tool_calls=calls)


def _tc(name, args, id):
    return {"name": name, "args": args, "id": id, "type": "tool_call"}


def _flask_repo(tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    (tmp_path / "requirements.txt").write_text("flask\n")
    return RepoTools(tmp_path)


def test_agent_explores_then_submits_a_service_plan(tmp_path):
    model = FakeChatModel([
        [_tc("read_file", {"path": "app.py"}, "t1")],            # explore
        [_tc("find", {"glob": "*.txt"}, "t2")],                   # explore more
        [_tc("submit_plan", {                                     # decide
            "classification": "service",
            "candidates": [{
                "image": "python:3.11-bookworm",
                "setup": ["pip install -r requirements.txt"],
                "start": "python app.py",
                "port": 8000,
            }],
            "rationale": "Flask app; app.py is the entry point",
        }, "t3")],
    ])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="tree...", repo=REPO)

    assert result.consulted is True
    assert result.classification == "service"
    assert len(result.candidates) == 1
    plan = result.candidates[0]
    assert plan.shape == RunShape.SERVICE
    app = plan.components[0]
    assert app.command == "pip install -r requirements.txt && python app.py"  # setup folded
    assert app.ports == [8000]
    normalize_plan(plan)
    assert model.invocations == 3  # explored twice, then submitted


def test_agent_can_declare_services_and_env(tmp_path):
    model = FakeChatModel([
        [_tc("submit_plan", {
            "classification": "service",
            "candidates": [{
                "image": "python:3.11-bookworm",
                "setup": ["pip install -r requirements.txt"],
                "start": "python app.py",
                "port": 8000,
                "services": [{
                    "name": "postgres", "image": "postgres:16",
                    "env": {"POSTGRES_PASSWORD": "app"}, "healthcheck": "pg_isready -U postgres",
                }],
                "env": {"DATABASE_URL": "postgresql://postgres:app@postgres:5432/postgres"},
            }],
        }, "t1")],
    ])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="s", repo=REPO)
    plan = result.candidates[0]
    normalize_plan(plan)
    comps = {c.name: c for c in plan.components}
    assert plan.shape == RunShape.MULTI_COMPONENT_SERVICE
    assert comps["postgres"].oracle.type == "native-cmd"
    assert comps["postgres"].oracle.command == "pg_isready -U postgres"
    assert comps["app"].env["DATABASE_URL"].startswith("postgresql://")


def test_agent_classifies_non_service_with_no_candidates(tmp_path):
    (tmp_path / "README.md").write_text("# skills\nmarkdown files\n")
    model = FakeChatModel([
        [_tc("list_dir", {"path": "."}, "t1")],
        [_tc("submit_plan", {"classification": "docs", "candidates": [], "rationale": "just markdown"}, "t2")],
    ])
    result = explore_and_plan(model, RepoTools(tmp_path), seed="tree", repo=REPO)
    assert result.classification == "docs"
    assert result.candidates == []


def test_agent_drops_incomplete_candidates(tmp_path):
    model = FakeChatModel([
        [_tc("submit_plan", {"classification": "service",
                             "candidates": [{"image": "python:3.11"}]}, "t1")],  # missing start
    ])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="s", repo=REPO)
    assert result.classification == "service"
    assert result.candidates == []


def test_a_malformed_candidate_is_dropped_not_fatal(tmp_path):
    # a bad port must drop only that candidate, keeping valid siblings + classification
    model = FakeChatModel([
        [_tc("submit_plan", {
            "classification": "service",
            "candidates": [
                {"image": "python:3.11", "start": "python app.py", "port": "nope"},
                {"image": "python:3.11", "start": "python app.py", "port": 8080},
            ],
        }, "t1")],
    ])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="s", repo=REPO)
    assert result.classification == "service"
    assert len(result.candidates) == 1
    assert result.candidates[0].components[0].ports == [8080]


def test_agent_returns_unknown_if_it_never_submits(tmp_path):
    # model keeps reading and never submits -> bounded, returns no candidates
    model = FakeChatModel([[_tc("list_dir", {"path": "."}, f"t{i}")] for i in range(40)])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="s", repo=REPO)
    assert result.candidates == []
    assert result.consulted is True


def test_agent_decomposes_a_repo_into_components(tmp_path):
    # a full-stack submission (db + backend) becomes a component Run Plan (#40)
    model = FakeChatModel([
        [_tc("submit_plan", {
            "classification": "service",
            "candidates": [{
                "components": [
                    {"name": "db", "role": "database", "image": "postgres:16",
                     "env": {"POSTGRES_PASSWORD": "app"},
                     "oracle": {"type": "native-cmd", "command": "pg_isready -U postgres"}},
                    {"name": "backend", "role": "backend", "image": "python:3.11",
                     "workdir": "/workspace/repo",
                     "command": "uvicorn app:app --host 0.0.0.0 --port 8000",
                     "ports": [8000], "depends_on": ["db"],
                     "env": {"DATABASE_URL": "postgresql://postgres:app@db:5432/postgres"},
                     "oracle": {"type": "http", "port": 8000, "path": "/health"}},
                ],
            }],
            "rationale": "flask + postgres full-stack",
        }, "t1")],
    ])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="tree", repo=REPO)

    assert result.classification == "service"
    plan = result.candidates[0]
    normalize_plan(plan)
    assert plan.shape == RunShape.MULTI_COMPONENT_SERVICE
    comps = {c.name: c for c in plan.components}
    assert comps["db"].oracle.type == "native-cmd"
    assert comps["backend"].depends_on == ["db"]
    # wiring: backend points at db by service name (#42)
    assert comps["backend"].env["DATABASE_URL"].endswith("@db:5432/postgres")
    assert comps["backend"].image == "python:3.11"
    assert comps["backend"].ports == [8000]


def test_agent_component_plan_needs_a_repo_code_component(tmp_path):
    # only a managed image, no component that runs the repo -> not runnable, dropped
    model = FakeChatModel([
        [_tc("submit_plan", {
            "classification": "service",
            "candidates": [{
                "components": [
                    {"name": "db", "image": "postgres:16",
                     "oracle": {"type": "native-cmd", "command": "pg_isready"}},
                ],
            }],
        }, "t1")],
    ])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="tree", repo=REPO)
    assert result.candidates == []


def test_agent_drops_component_with_invalid_oracle(tmp_path):
    model = FakeChatModel([
        [_tc("submit_plan", {
            "classification": "service",
            "candidates": [{
                "components": [
                    {"name": "backend", "image": "python:3.11", "workdir": "/workspace/repo",
                     "command": "python app.py", "ports": [8000],
                     "oracle": {"type": "telepathy"}},  # invalid -> component dropped
                ],
            }],
        }, "t1")],
    ])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="tree", repo=REPO)
    # the only component was dropped -> no valid components -> candidate dropped
    assert result.candidates == []


def test_agent_exercises_a_cli_as_a_component(tmp_path):
    # a non-service repo still succeeds by being run: a CLI subcommand, oracle
    # functional-smoke (#41)
    model = FakeChatModel([
        [_tc("submit_plan", {
            "classification": "cli",
            "candidates": [{
                "components": [
                    {"name": "cli", "role": "cli", "image": "python:3.11",
                     "workdir": "/workspace/repo",
                     "command": "pip install -e . && mytool convert sample.txt",
                     "oracle": {"type": "functional-smoke"}},
                ],
            }],
            "rationale": "console_scripts entry point; run a real subcommand",
        }, "t1")],
    ])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="tree", repo=REPO)

    assert result.classification == "cli"
    plan = result.candidates[0]
    normalize_plan(plan)
    assert plan.shape == RunShape.CLI
    assert plan.components[0].oracle.type == "functional-smoke"
    assert "mytool convert" in plan.components[0].command


def test_agent_can_classify_build_and_batch(tmp_path):
    # build/batch are valid classifications now (not downgraded to unknown)
    for shape in ("build", "batch"):
        model = FakeChatModel([
            [_tc("submit_plan", {"classification": shape, "candidates": []}, "t1")],
        ])
        result = explore_and_plan(model, RepoTools(tmp_path), seed="s", repo=REPO)
        assert result.classification == shape
