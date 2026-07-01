"""Provider-agnostic model client (ADR-0005): dispatch + real construction.

Record/replay keeps this token-free; real providers are built via LangChain
init_chat_model (construction needs no API key — the key is used at call time).
"""

from repo_pilot.config import load_config
from repo_pilot.model_client import (
    LangChainModelClient,
    ReplayModelClient,
    build_model_client,
)


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
    cfg = load_config(overrides={"model": {"provider": "openai", "model_id": "gpt-x"}})
    assert cfg.model.provider == "openai" and cfg.model.model_id == "gpt-x"


def test_replay_client_records_prompts_and_returns_queued():
    client = ReplayModelClient(["a", "b"])
    assert client.complete("p1") == "a"
    assert client.complete("p2") == "b"
    assert client.calls == ["p1", "p2"]
