"""Headline fetch and a small lexicon. Missing news is neutral, not a veto."""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")

POSITIVE = frozenset({
    "upgrade", "beat", "beats", "record", "surge", "surges", "rally",
    "bullish", "breakthrough", "outperform", "strong", "approval",
})
NEGATIVE = frozenset({
    "downgrade", "fraud", "lawsuit", "investigation", "probe", "recall",
    "bankruptcy", "plunge", "plunges", "layoffs", "slump", "scandal",
    "subpoena", "miss", "misses", "warning", "bearish",
})


def fetch_headlines(symbol: str, *, limit: int = 3) -> list[str]:
    """Best-effort yfinance headlines. Any failure returns an empty list."""
    try:
        import yfinance as yf

        items = yf.Ticker(symbol).news or []
    except Exception:
        return []
    titles: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not title and isinstance(item.get("content"), dict):
            title = item["content"].get("title")
        if title:
            titles.append(str(title).strip()[:120])
        if len(titles) >= limit:
            break
    return titles


def score_headlines(headlines: list[str]) -> float | None:
    """Return -1..1, 0 when headlines exist but carry no lexicon hits, None when there is no news."""
    if not headlines:
        return None
    positive = 0
    negative = 0
    for title in headlines:
        tokens = set(_TOKEN_RE.findall(title.lower()))
        positive += len(tokens & POSITIVE)
        negative += len(tokens & NEGATIVE)
    if positive + negative == 0:
        return 0.0
    return round((positive - negative) / (positive + negative), 4)
