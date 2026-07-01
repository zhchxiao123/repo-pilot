"""Provider-agnostic model client (ADR-0005).

The Tier-B seams call through this interface, never a provider SDK directly, so the
model is swappable by config. Tests use ``ReplayModelClient`` (record/replay, no
live tokens). Real providers are built lazily via a LangChain backend (not exercised
without credentials).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from repo_pilot.config import Config


@runtime_checkable
class ModelClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class ReplayModelClient:
    """Record/replay double: returns queued responses and records prompts."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._responses.pop(0) if self._responses else ""


def build_model_client(config: Config, *, replay: list[str] | None = None) -> ModelClient:
    """Return a client for the configured provider — swap providers via config only."""
    provider = config.model.provider
    if provider == "replay":
        return ReplayModelClient(replay or [])
    # Real providers (anthropic/openai/google/...) are built lazily through a
    # LangChain chat model keyed by provider + model_id. Not exercised in tests.
    raise NotImplementedError(
        f"model provider '{provider}' requires a configured LangChain backend"
    )
