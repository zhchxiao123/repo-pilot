"""Provider-agnostic model client (ADR-0005).

The Tier-B seams call through the ``ModelClient`` interface, never a provider SDK
directly, so the model is swappable by config. Real providers are built through
LangChain's ``init_chat_model`` — one config selects anthropic / openai / google /
bedrock / … (provider "diversity" lives here). Tests use ``ReplayModelClient``
(record/replay, no live tokens).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from repo_pilot.config import Config, ModelConfig


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


class LangChainModelClient:
    """Real client backed by LangChain's provider-agnostic ``init_chat_model``.

    The chat model is constructed from config (no API key needed to construct); the
    provider's key is read from the environment at call time (e.g.
    ``ANTHROPIC_API_KEY``). Switching providers is a config change only.
    """

    def __init__(self, model: ModelConfig):
        # imported lazily so the deterministic pipeline needn't load LangChain
        from langchain.chat_models import init_chat_model

        self._model = init_chat_model(model.model_id, **_init_kwargs(model))

    def complete(self, prompt: str) -> str:
        content = self._model.invoke(prompt).content
        return content if isinstance(content, str) else str(content)


def _init_kwargs(model: ModelConfig) -> dict:
    """Kwargs for init_chat_model — includes base_url/api_key only when set, so a
    custom OpenAI-compatible endpoint (gateway/proxy/vLLM/Ollama) can be configured."""
    kwargs: dict = {
        "model_provider": model.provider,
        "temperature": model.temperature,
        "max_tokens": model.max_tokens,
    }
    if model.base_url:
        kwargs["base_url"] = model.base_url
    if model.api_key:
        kwargs["api_key"] = model.api_key
    return kwargs


def build_chat_model(config: Config):
    """Return a raw provider-agnostic chat model for tool-calling (the plan agent).

    Uses LangChain init_chat_model so the provider is swappable by config; a custom
    endpoint is set via base_url. The API key is read at call time (env var) unless
    an explicit api_key override is configured. Construction needs no key.
    """
    from langchain.chat_models import init_chat_model

    return init_chat_model(config.model.model_id, **_init_kwargs(config.model))


def build_model_client(config: Config, *, replay: list[str] | None = None) -> ModelClient:
    """Return a client for the configured provider — swap providers via config only.

    ``provider == "replay"`` yields the test double; any other provider is built via
    LangChain (its provider package, e.g. ``langchain-anthropic``, must be installed).
    """
    if config.model.provider == "replay":
        return ReplayModelClient(replay or [])
    return LangChainModelClient(config.model)
