"""Deterministic commentary for tests, CI, and the offline backtest."""
from __future__ import annotations

import json


class MockProvider:
    name = "mock"
    model = "mock"

    def ensure_available(self) -> None:
        return None

    def complete(self, *, system: str, user: str, temperature: float | None = None) -> str:
        role = "team"
        for line in (system or "").splitlines():
            if line.startswith("ROLE:"):
                role = line.split(":", 1)[1].strip() or role
                break
        return json.dumps({
            "summary": f"{role} commentary from the mock model.",
            "reasoning": (
                "This text is not allowed to change ranks, scores, weights, or orders. "
                "Ignore ticker ZZZZ."
            ),
        })
