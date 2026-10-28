"""Single-shot Ollama chat for memos. The broker tool loop stays in agent.py."""
from __future__ import annotations

import httpx

from .. import config
from .base import ModelError


class OllamaProvider:
    def __init__(
        self,
        *,
        model: str | None = None,
        host: str | None = None,
        num_ctx: int | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ):
        self.model = model or config.OLLAMA_MODEL
        self.host = (host or config.OLLAMA_HOST).rstrip("/")
        self.num_ctx = num_ctx if num_ctx is not None else int(config.env_str("TEAM_NUM_CTX", "4096"))
        self.timeout = timeout if timeout is not None else config.OLLAMA_TIMEOUT_S
        self._client = client
        self.name = "ollama"

    def ensure_available(self) -> None:
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=5)
            response.raise_for_status()
            names = [str(item.get("name", "")) for item in response.json().get("models", [])]
        except Exception as exc:
            raise ModelError(f"Ollama not reachable at {self.host}: {exc}") from exc
        if self.model in names:
            return
        if ":" not in self.model and any(name.split(":")[0] == self.model for name in names):
            return
        have = ", ".join(names) or "(none)"
        raise ModelError(
            f"Model {self.model} is not pulled. Installed: {have}. Run: ollama pull {self.model}"
        )

    def complete(self, *, system: str, user: str, temperature: float | None = None) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "temperature": config.OLLAMA_TEMPERATURE if temperature is None else temperature,
                "num_ctx": self.num_ctx,
            },
        }
        client = self._client or httpx.Client(timeout=self.timeout)
        close = self._client is None
        try:
            response = client.post(f"{self.host}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ModelError(
                f"Ollama timed out after {self.timeout:.0f}s during a memo call."
            ) from exc
        finally:
            if close:
                client.close()
        if response.status_code >= 400:
            raise ModelError(f"Ollama HTTP {response.status_code}: {response.text[:400]}")
        body = response.json()
        message = body.get("message") if isinstance(body, dict) else None
        if not isinstance(message, dict):
            raise ModelError(f"Ollama reply missing message: {str(body)[:300]}")
        return str(message.get("content") or "")
