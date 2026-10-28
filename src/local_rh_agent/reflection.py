"""Score the previous decision against realized prices and append a lesson."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from .memory import append_capped, load_jsonl, render_lessons
from .team.commentary import write_commentary
from .team.risk import LEVERAGE_LOSS_LIMIT_PCT, TIGHT_LEVERAGE_CAP

Quote = Callable[[str, date, date], float | None]


def find_prior_decision(decisions: list[dict[str, Any]], as_of: date) -> dict[str, Any] | None:
    stamp = as_of.isoformat()
    for row in reversed(decisions):
        if row.get("as_of") and row.get("as_of") != stamp:
            return row
    return None


def score_decision(
    prior: dict[str, Any],
    *,
    as_of: date,
    quote: Quote,
    spy_symbol: str = "SPY",
) -> dict[str, Any] | None:
    """Percent returns. None when a held symbol cannot be priced (so we do not invent a lesson)."""
    start = date.fromisoformat(str(prior["as_of"]))
    if as_of <= start:
        return None
    orders = list(prior.get("orders") or [])
    names: list[dict[str, Any]] = []
    book = 0.0
    for order in orders:
        returned = quote(str(order["symbol"]), start, as_of)
        if returned is None:
            return None
        weight = float(order.get("weight") or 0)
        names.append({
            "symbol": order["symbol"],
            "underlying": order.get("underlying"),
            "weight": weight,
            "leveraged": bool(order.get("leveraged")),
            "return_pct": round(float(returned), 4),
        })
        book += weight * float(returned)
    leveraged = _sleeve_return(orders, quote, start, as_of)
    spy = quote(spy_symbol, start, as_of)
    return {
        "book_return_pct": round(book, 4),
        "spy_return_pct": None if spy is None else round(float(spy), 4),
        "leveraged_return_pct": None if leveraged is None else round(leveraged, 4),
        "names": sorted(names, key=lambda row: row["return_pct"]),
    }


def _sleeve_return(orders: list[dict[str, Any]], quote: Quote, start: date, end: date) -> float | None:
    sleeve = [order for order in orders if order.get("leveraged") and float(order.get("weight") or 0) > 0]
    if not sleeve:
        return None
    total = sum(float(order["weight"]) for order in sleeve)
    if total <= 0:
        return None
    acc = 0.0
    for order in sleeve:
        returned = quote(str(order["symbol"]), start, end)
        if returned is None:
            return None
        acc += float(order["weight"]) / total * float(returned)
    return acc


def lesson_from_score(prior: dict[str, Any], as_of: date, score: dict[str, Any]) -> dict[str, Any]:
    book = float(score["book_return_pct"])
    spy = score["spy_return_pct"]
    leveraged = score["leveraged_return_pct"]
    spy_text = "n/a" if spy is None else f"{float(spy):.1f}%"
    parts = [
        f"From {prior['as_of']} to {as_of.isoformat()}, the book returned {book:.1f}% versus SPY {spy_text}."
    ]
    if leveraged is not None:
        parts.append(f"The leveraged sleeve returned {float(leveraged):.1f}%.")
        if float(leveraged) <= LEVERAGE_LOSS_LIMIT_PCT:
            parts.append(
                f"The next run caps leveraged ETFs at {TIGHT_LEVERAGE_CAP:.0%} "
                f"because that sleeve lost more than {abs(LEVERAGE_LOSS_LIMIT_PCT):.0f}%."
            )
    if score["names"]:
        worst = score["names"][0]
        parts.append(f"Weakest holding: {worst['symbol']} {float(worst['return_pct']):.1f}%.")
    return {
        "as_of": as_of.isoformat(),
        "prior_as_of": prior["as_of"],
        "scored_on": as_of.isoformat(),
        "book_return_pct": score["book_return_pct"],
        "spy_return_pct": score["spy_return_pct"],
        "leveraged_return_pct": score["leveraged_return_pct"],
        "lesson": " ".join(parts),
        "names": score["names"],
    }


def grade(
    *,
    as_of: date,
    decisions_path: Path,
    lessons_path: Path,
    quote: Quote | None,
    provider: Any = None,
) -> dict[str, Any]:
    """Append one lesson about the previous decision. Safe to call again for the same pair of dates."""
    prior = find_prior_decision(load_jsonl(decisions_path), as_of)
    if prior is None:
        return {"wrote_lesson": False, "reason": "no prior decision"}
    existing = load_jsonl(lessons_path)
    for item in existing:
        if item.get("prior_as_of") == prior.get("as_of") and item.get("scored_on") == as_of.isoformat():
            return {"wrote_lesson": False, "reason": "already scored", "lesson": item}
    if quote is None:
        return {"wrote_lesson": False, "reason": "prices unavailable"}
    try:
        score = score_decision(prior, as_of=as_of, quote=quote)
    except Exception:
        score = None
    if score is None:
        return {"wrote_lesson": False, "reason": "prices unavailable"}
    lesson = lesson_from_score(prior, as_of, score)
    if provider is not None:
        known = {str(row.get("symbol") or "") for row in lesson["names"]}
        known.add("SPY")
        note = write_commentary(
            provider,
            role="reflection",
            prompt_facts={"lesson": lesson["lesson"]},
            lessons=[],
            known_symbols=known,
        )
        lesson["model_commentary"] = note["reasoning"]
        lesson["commentary_source"] = note["source"]
    rows = append_capped(lessons_path, lesson)
    lessons_path.with_suffix(".md").write_text(render_lessons(rows))
    return {"wrote_lesson": True, "lesson": lesson}


def yfinance_quote(symbol: str, start: date, end: date) -> float | None:
    """Percent price change across the closes in [start, end]. Failures return None."""
    try:
        import yfinance as yf

        frame = yf.download(
            symbol,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception:
        return None
    if frame is None or len(frame) == 0:
        return None
    try:
        close = frame["Close"]
    except (KeyError, TypeError):
        return None
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    close = close.dropna()
    if len(close) < 1:
        return None
    first = float(close.iloc[0])
    last = float(close.iloc[-1])
    if first <= 0:
        return None
    return (last / first - 1.0) * 100.0
