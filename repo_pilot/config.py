"""Runtime configuration for repo-pilot.

The model client is provider-agnostic (ADR-0005): the provider and model id are
resolved from defaults, an optional overrides dict, and environment variables — so
the model is swappable by config, never by code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

DEFAULT_MODEL_PROVIDER = "anthropic"
DEFAULT_MODEL_ID = "claude-opus-4-8"


@dataclass(frozen=True)
class ModelConfig:
    provider: str = DEFAULT_MODEL_PROVIDER
    model_id: str = DEFAULT_MODEL_ID
    temperature: float = 0.0
    max_tokens: int = 2048


@dataclass(frozen=True)
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    artifacts_root: str = "artifacts"


def load_config(overrides: dict | None = None) -> Config:
    """Resolve config from defaults, then env vars, then explicit overrides.

    Precedence (low to high): dataclass defaults < environment < overrides.
    """
    model = ModelConfig()

    env_provider = os.environ.get("REPO_PILOT_MODEL_PROVIDER")
    env_model_id = os.environ.get("REPO_PILOT_MODEL_ID")
    env_temperature = os.environ.get("REPO_PILOT_MODEL_TEMPERATURE")
    env_max_tokens = os.environ.get("REPO_PILOT_MODEL_MAX_TOKENS")
    if env_provider is not None:
        model = replace(model, provider=env_provider)
    if env_model_id is not None:
        model = replace(model, model_id=env_model_id)
    if env_temperature is not None:
        model = replace(model, temperature=float(env_temperature))
    if env_max_tokens is not None:
        model = replace(model, max_tokens=int(env_max_tokens))

    artifacts_root = os.environ.get("REPO_PILOT_ARTIFACTS_ROOT", "artifacts")
    config = Config(model=model, artifacts_root=artifacts_root)

    if overrides:
        model_over = overrides.get("model", {})
        if model_over:
            config = replace(config, model=replace(config.model, **model_over))
        if "artifacts_root" in overrides:
            config = replace(config, artifacts_root=overrides["artifacts_root"])

    return config
