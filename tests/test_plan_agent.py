"""The plan agent explores via tools then submits a classification + run plans."""

from langchain_core.messages import AIMessage

from repo_pilot.explore_tools import RepoTools
from repo_pilot.plan_agent import explore_and_plan
from repo_pilot.schemas import validate_runbook

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
    rb = result.candidates[0]
    assert rb["steps"]["start"][0]["command"] == "python app.py"
    assert rb["steps"]["start"][0]["expected_ports"] == [8000]
    validate_runbook(rb)
    assert model.invocations == 3  # explored twice, then submitted


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
    assert result.candidates[0]["steps"]["start"][0]["expected_ports"] == [8080]


def test_agent_returns_unknown_if_it_never_submits(tmp_path):
    # model keeps reading and never submits -> bounded, returns no candidates
    model = FakeChatModel([[_tc("list_dir", {"path": "."}, f"t{i}")] for i in range(40)])
    result = explore_and_plan(model, _flask_repo(tmp_path), seed="s", repo=REPO)
    assert result.candidates == []
    assert result.consulted is True
