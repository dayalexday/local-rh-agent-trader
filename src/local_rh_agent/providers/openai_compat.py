"""OpenAI-compatible chat completions. Works with OpenAI, LM Studio, llama.cpp, vLLM."""
from __future__ import annotations

import os

import httpx

from .. import config
from .base import ModelError


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ):
        self.model = model or config.env_str("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or config.env_str("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.timeout = timeout if timeout is not None else config.env_float("OPENAI_TIMEOUT_S", 60.0)
        self._client = client
        self.name = "openai"

    def ensure_available(self) -> None:
        if not str(self.api_key or "").strip():
            raise ModelError(
                "OPENAI_API_KEY is empty. Set it in .env, or use MODEL_PROVIDER=ollama or mock."
            )

    def complete(self, *, system: str, user: str, temperature: float | None = None) -> str:
        self.ensure_available()
        payload = {
            "model": self.model,
            "temperature": config.OLLAMA_TEMPERATURE if temperature is None else temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client = self._client or httpx.Client(timeout=self.timeout)
        close = self._client is None
        try:
            response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ModelError(f"Hosted model timed out after {self.timeout:.0f}s.") from exc
        finally:
            if close:
                client.close()
        if response.status_code >= 400:
            raise ModelError(f"Hosted model HTTP {response.status_code}: {response.text[:400]}")
        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError(f"Hosted model reply was not a chat completion: {str(body)[:300]}") from exc
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or ""))
                else:
                    parts.append(str(part))
            content = "".join(parts)
        return str(content or "")
