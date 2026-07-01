"""Config resolves a model provider, swappable without code changes (ADR-0005)."""

from repo_pilot.config import load_config


def test_default_model_provider_is_claude_opus():
    cfg = load_config()
    assert cfg.model.provider == "anthropic"
    assert cfg.model.model_id == "claude-opus-4-8"


def test_model_provider_is_overridable_without_code_change():
    cfg = load_config(overrides={"model": {"provider": "openai", "model_id": "gpt-x"}})
    assert cfg.model.provider == "openai"
    assert cfg.model.model_id == "gpt-x"


def test_model_provider_overridable_from_env(monkeypatch):
    monkeypatch.setenv("REPO_PILOT_MODEL_PROVIDER", "google")
    monkeypatch.setenv("REPO_PILOT_MODEL_ID", "gemini-x")
    cfg = load_config()
    assert cfg.model.provider == "google"
    assert cfg.model.model_id == "gemini-x"
