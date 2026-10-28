"""Portfolio manager. Orders are a copy of the risk book, never a new idea from the model."""
from __future__ import annotations

from typing import Any

from .commentary import write_commentary
from .memo import Memo


def build_orders(decision: dict[str, Any]) -> list[dict[str, Any]]:
    approved = {
        pos["underlying"]: pos
        for pos in decision["positions"]
        if pos["action"] != "veto" and float(pos["weight"]) > 0 and float(pos["illustrative_dollars"]) > 0
    }
    orders: list[dict[str, Any]] = []
    for pos in decision["positions"]:
        if pos["underlying"] not in approved:
            continue
        allowed = approved[pos["underlying"]]
        if pos["symbol"] != allowed["symbol"]:
            raise RuntimeError(f"Refusing {pos['symbol']}: risk approved {allowed['symbol']}.")
        dollars = float(pos["illustrative_dollars"])
        if dollars - 1e-6 > float(allowed["illustrative_dollars"]):
            raise RuntimeError(f"Refusing {pos['symbol']}: dollars exceed the risk cap.")
        orders.append({
            "symbol": pos["symbol"],
            "underlying": pos["underlying"],
            "side": "buy",
            "type": "market",
            "dollar_amount": f"{dollars:.2f}",
            "weight": float(pos["weight"]),
            "leveraged": bool(pos["leveraged"]),
            "sector": pos["sector"],
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
        })
    return orders


def _markdown(orders: list[dict[str, Any]], blocked: list[dict[str, Any]], cash: float) -> str:
    lines = [
        "The executor sells the current equity book first, then buys only this list.",
        "Dollar amounts illustrate the paper buying power. A live run rescales to the account and keeps the weights.",
        f"Cash weight {cash:.1%}.",
        "",
        "| Symbol | Underlying | Weight | Dollars | Leveraged | Sector |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    if not orders:
        lines.append("| — | — | — | — | — | — |")
    for order in orders:
        lines.append(
            f"| {order['symbol']} | {order['underlying']} | {order['weight']:.1%} | "
            f"{order['dollar_amount']} | {order['leveraged']} | {order['sector']} |"
        )
    if blocked:
        lines.extend(["", "Blocked by risk:", ""])
        for pos in blocked:
            lines.append(f"- {pos['underlying']}: {pos.get('reason') or pos.get('action')}")
    return "\n".join(lines)


def analyze(
    decision: dict[str, Any],
    *,
    provider: Any,
    lessons: list[dict[str, Any]],
    as_of: str,
) -> Memo:
    orders = build_orders(decision)
    blocked = [pos for pos in decision["positions"] if pos["action"] == "veto" or pos["weight"] <= 0]
    facts: dict[str, Any] = {
        "decision": decision["decision"],
        "orders": orders,
        "sell_book_first": bool(orders),
        "cash_weight": decision["cash_weight"],
        "blocked": [
            {"underlying": pos["underlying"], "reason": pos["reason"], "action": pos["action"]}
            for pos in blocked
        ],
        "note": (
            "Illustrative dollars use the paper buying power. "
            "The guarded executor is the only component that may call Robinhood order tools."
        ),
    }
    known = {order["symbol"] for order in orders} | {order["underlying"] for order in orders}
    known |= {pos["underlying"] for pos in blocked}
    note = write_commentary(
        provider,
        role="portfolio",
        prompt_facts={
            "decision": decision["decision"],
            "orders": [
                {"symbol": order["symbol"], "weight": order["weight"], "dollar_amount": order["dollar_amount"]}
                for order in orders
            ],
            "blocked": [pos["underlying"] for pos in blocked],
        },
        lessons=lessons,
        known_symbols=known,
    )
    facts["model_summary"] = note["model_summary"]
    facts["ignored_tickers"] = note["ignored_tickers"]
    if orders:
        blocked_count = len(blocked)
        blocked_word = "name" if blocked_count == 1 else "names"
        summary = (
            f"Propose {len(orders)} buys. {blocked_count} {blocked_word} blocked. "
            f"Cash {decision['cash_weight']:.0%}."
        )
    else:
        summary = "No orders. Risk left an empty book."
    return Memo(
        agent="portfolio",
        as_of=as_of,
        title="Portfolio manager",
        summary=summary,
        reasoning=note["reasoning"],
        facts=facts,
        provider=getattr(provider, "name", ""),
        model=getattr(provider, "model", ""),
        commentary_source=note["source"],
    )


def markdown_body(memo: Memo) -> str:
    return _markdown(memo.facts["orders"], memo.facts["blocked"], float(memo.facts["cash_weight"]))
