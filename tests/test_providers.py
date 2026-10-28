import json

import httpx
import pytest

from local_rh_agent.providers import get_provider
from local_rh_agent.providers.base import ModelError
from local_rh_agent.providers.mock import MockProvider
from local_rh_agent.providers.ollama import OllamaProvider
from local_rh_agent.providers.openai_compat import OpenAICompatibleProvider
from local_rh_agent.team.commentary import parse_commentary


def test_provider_switch(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    assert isinstance(get_provider(), MockProvider)

    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    hosted = get_provider(role="risk")
    assert isinstance(hosted, OpenAICompatibleProvider)

    monkeypatch.setenv("PORTFOLIO_PROVIDER", "mock")
    assert isinstance(get_provider(role="portfolio"), MockProvider)
    assert isinstance(get_provider(role="risk"), OpenAICompatibleProvider)

    monkeypatch.setenv("RISK_MODEL", "gpt-test")
    assert get_provider(role="risk").model == "gpt-test"

    monkeypatch.setenv("MOMENTUM_PROVIDER", "openai")
    assert isinstance(get_provider("mock", role="momentum"), MockProvider)
    assert isinstance(get_provider("local", role="momentum"), OllamaProvider)


def test_unknown_provider_is_rejected():
    with pytest.raises(ModelError):
        get_provider("not-a-backend")


def test_openai_compatible_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"summary": "Hello", "reasoning": "Because"}'}}],
        })

    provider = OpenAICompatibleProvider(
        model="gpt-test",
        base_url="https://example.invalid/v1",
        api_key="sk-test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    text = provider.complete(system="ROLE: risk", user="facts")
    assert "Hello" in text
    assert seen["url"] == "https://example.invalid/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "gpt-test"
    assert seen["body"]["messages"][0]["role"] == "system"
    assert "tools" not in seen["body"]


def test_openai_requires_a_key():
    provider = OpenAICompatibleProvider(model="gpt-test", api_key="", base_url="https://example.invalid/v1")
    with pytest.raises(ModelError):
        provider.complete(system="ROLE: risk", user="facts")


def test_ollama_memo_call_has_no_tools():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "{\"summary\":\"s\",\"reasoning\":\"r\"}"}})

    provider = OllamaProvider(
        model="qwen2.5:3b",
        host="http://127.0.0.1:11434",
        num_ctx=4096,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    text = provider.complete(system="ROLE: momentum", user="{}")
    assert "s" in text
    assert seen["path"] == "/api/chat"
    assert "tools" not in seen["body"]
    assert seen["body"]["stream"] is False
    assert seen["body"]["options"]["num_ctx"] == 4096


def test_parse_fenced_json_and_garbage():
    summary, reasoning, source = parse_commentary('```json\n{"summary": "s", "reasoning": "r"}\n```')
    assert (summary, reasoning, source) == ("s", "r", "model")
    summary, reasoning, source = parse_commentary("no json here")
    assert source == "fallback"
    assert "no json" in reasoning
    assert summary == ""
