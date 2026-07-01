"""Provider-agnostic model client + NL-prose extraction seam (ADR-0004/0005).

Tier-B fallback: fires only when deterministic extraction finds nothing, is
schema-constrained, and its output is subordinate to the sandbox (tested elsewhere).
Record/replay means no live tokens.
"""

import json

from repo_pilot.config import load_config
from repo_pilot.model_client import (
    LangChainModelClient,
    ReplayModelClient,
    build_model_client,
)
from repo_pilot.nl_extract import nl_extract_commands


def test_build_model_client_dispatches_on_config_provider():
    cfg = load_config(overrides={"model": {"provider": "replay"}})
    client = build_model_client(cfg, replay=["[]"])
    assert isinstance(client, ReplayModelClient)


def test_build_model_client_constructs_a_real_provider_client():
    # anthropic is the default; construction needs no API key (key used at call time)
    cfg = load_config()  # provider=anthropic, model=claude-opus-4-8
    client = build_model_client(cfg)
    assert isinstance(client, LangChainModelClient)


def test_model_provider_is_swappable_by_config_only():
    # switching provider is a config change; the model config carries it through
    cfg = load_config(overrides={"model": {"provider": "openai", "model_id": "gpt-x"}})
    assert cfg.model.provider == "openai" and cfg.model.model_id == "gpt-x"


def test_nl_extract_parses_commands_from_model_output():
    client = ReplayModelClient([json.dumps(["python app.py"])])
    commands = nl_extract_commands("Run `python app.py` to start.", client)
    assert commands == ["python app.py"]
    assert client.calls  # the model was actually consulted


def test_nl_extract_returns_empty_on_unparseable_output():
    client = ReplayModelClient(["not json at all"])
    assert nl_extract_commands("whatever", client) == []


def test_nl_extract_ignores_non_string_items():
    client = ReplayModelClient([json.dumps(["ok", 123, {"x": 1}])])
    assert nl_extract_commands("x", client) == ["ok"]
