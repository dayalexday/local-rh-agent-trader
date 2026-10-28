"""Ask a model for prose. The reply is never parsed into positions."""
from __future__ import annotations

import json
import re
from typing import Any

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")
_STOP = frozenset(
    """
    A I AN THE AND FOR TOP CAP DRY RUN ETF ETFS SPY NYSE JSON USD GFD MAC LLM
    API CI OK PM SEC USA NOT THIS TEXT FROM INTO WITH THAT YOUR ONLY MEMO
    ROLE TEAM
    """.split()
)


def untrusted_tickers(text: str, known: set[str]) -> list[str]:
    found = set(_TICKER_RE.findall(text or ""))
    return sorted(found - {symbol.upper() for symbol in known} - _STOP)


def parse_commentary(raw: str) -> tuple[str, str, str]:
    """Return model summary, reasoning, and source (`model` or `fallback`)."""
    text = _THINK_RE.sub("", raw or "").strip()
    fenced = _FENCE_RE.sub("", text).strip()
    start = fenced.find("{")
    end = fenced.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(fenced[start : end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            summary = str(data.get("summary") or "").strip()[:500]
            reasoning = str(data.get("reasoning") or "").strip()[:2000]
            if summary or reasoning:
                return summary, reasoning or "No reasoning returned.", "model"
    if not text:
        return "", "The model returned an empty reply.", "fallback"
    return "", text[:800], "fallback"


def write_commentary(
    provider: Any,
    *,
    role: str,
    prompt_facts: dict[str, Any],
    lessons: list[dict[str, Any]],
    known_symbols: set[str],
) -> dict[str, Any]:
    system = (
        f"ROLE: {role}\n"
        "You write a short memo for a human reader. Reply with one JSON object only:\n"
        '{"summary": "...", "reasoning": "..."}\n'
        "Do not add tickers, weights, or orders. Facts in the user message are final.\n"
        "Keep the reasoning under 120 words."
    )
    recent: list[str] = []
    for lesson in lessons[-3:]:
        text = str(lesson.get("lesson") or "").strip()[:500]
        if text:
            recent.append(text)
    user = json.dumps({"facts": prompt_facts, "recent_lessons": recent}, default=str)
    try:
        raw = provider.complete(system=system, user=user)
        summary, reasoning, source = parse_commentary(raw)
    except Exception as exc:
        summary, reasoning, source = "", f"Model provider failed ({exc}). The book was not changed.", "fallback"
    blob = f"{summary}\n{reasoning}"
    return {
        "model_summary": summary,
        "reasoning": reasoning,
        "source": source,
        "ignored_tickers": untrusted_tickers(blob, known_symbols),
    }
