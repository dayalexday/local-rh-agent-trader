"""Resolve MODEL_PROVIDER / ROLE_PROVIDER to a memo backend."""
from __future__ import annotations

import os

from .. import config
from .base import ModelError, ModelProvider
from .mock import MockProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatibleProvider

_ALIASES = {
    "ollama": "ollama",
    "local": "ollama",
    "openai": "openai",
    "hosted": "openai",
    "openai-compatible": "openai",
    "openai_compatible": "openai",
    "mock": "mock",
    "deterministic": "mock",
}


def get_provider(name: str | None = None, *, role: str = "team") -> ModelProvider:
    """Pick a backend. An explicit name overrides the role env var."""
    raw = (name or config.provider_name(role)).strip().lower()
    resolved = _ALIASES.get(raw)
    if resolved is None:
        raise ModelError(f"Unknown model provider {raw!r}. Use ollama, openai, or mock.")
    override = os.getenv(f"{role.upper()}_MODEL", "").strip() or None
    if resolved == "mock":
        return MockProvider()
    if resolved == "ollama":
        return OllamaProvider(model=override or config.OLLAMA_MODEL)
    return OpenAICompatibleProvider(model=override or config.env_str("OPENAI_MODEL", "gpt-4o-mini"))


__all__ = [
    "ModelError",
    "ModelProvider",
    "MockProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "get_provider",
]
