"""Model backends for research memos. These clients have no broker tools."""
from __future__ import annotations

from typing import Protocol


class ModelError(RuntimeError):
    pass


class ModelProvider(Protocol):
    name: str
    model: str

    def complete(self, *, system: str, user: str, temperature: float | None = None) -> str:
        """Return assistant text. No tool calls."""

    def ensure_available(self) -> None:
        """Raise ModelError when this backend cannot answer."""
