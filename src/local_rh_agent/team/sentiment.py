"""News and sentiment. Scores are the lexicon; the model only comments."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .commentary import write_commentary
from .memo import Memo
from .news import fetch_headlines, score_headlines


def score_names(
    candidates: list[dict[str, Any]],
    headline_source: Callable[[str], list[str]] | None = None,
) -> list[dict[str, Any]]:
    source = headline_source or fetch_headlines
    scored: list[dict[str, Any]] = []
    for row in candidates:
        symbol = str(row["underlying"])
        try:
            headlines = [str(item) for item in (source(symbol) or [])]
        except Exception:
            headlines = []
        scored.append({
            **row,
            "headlines": headlines[:4],
            "sentiment_score": score_headlines(headlines),
            "news_available": bool(headlines),
        })
    return scored


def _markdown(names: list[dict[str, Any]]) -> str:
    lines = [
        "Scores are a lexicon over headlines. No headlines means no score, and risk will not veto the name.",
        "",
        "| Underlying | Score | Headlines |",
        "| --- | ---: | --- |",
    ]
    for row in names:
        score = row.get("sentiment_score")
        shown = "n/a" if score is None else f"{float(score):.2f}"
        headlines = "; ".join(row.get("headlines") or []) or "—"
        headlines = headlines.replace("|", "/")
        lines.append(f"| {row['underlying']} | {shown} | {headlines} |")
    return "\n".join(lines)


def analyze(
    candidates: list[dict[str, Any]],
    *,
    provider: Any,
    lessons: list[dict[str, Any]],
    as_of: str,
    headline_source: Callable[[str], list[str]] | None = None,
) -> Memo:
    names = score_names(candidates, headline_source)
    unavailable = sum(1 for row in names if not row["news_available"])
    prompt_names = [
        {
            "underlying": row["underlying"],
            "sentiment_score": row["sentiment_score"],
            "headline": (row["headlines"][0] if row["headlines"] else ""),
        }
        for row in names
    ]
    known = {row["underlying"] for row in names}
    note = write_commentary(
        provider,
        role="sentiment",
        prompt_facts={"names": prompt_names, "unavailable": unavailable},
        lessons=lessons,
        known_symbols=known,
    )
    facts: dict[str, Any] = {
        "names": names,
        "unavailable": unavailable,
        "method": "lexicon on headlines; missing news is neutral and is not a veto",
        "model_summary": note["model_summary"],
        "ignored_tickers": note["ignored_tickers"],
    }
    summary = f"{len(names)} names scored. {unavailable} had no headlines."
    return Memo(
        agent="sentiment",
        as_of=as_of,
        title="News and sentiment",
        summary=summary,
        reasoning=note["reasoning"],
        facts=facts,
        provider=getattr(provider, "name", ""),
        model=getattr(provider, "model", ""),
        commentary_source=note["source"],
    )


def markdown_body(memo: Memo) -> str:
    return _markdown(memo.facts["names"])
