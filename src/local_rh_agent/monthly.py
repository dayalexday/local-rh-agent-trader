"""Monthly momentum rebalance: calendar gate, rank, then the local model trades."""
from __future__ import annotations

import json
from datetime import date, datetime

from . import config
from .agent import Agent
from .mcp_client import RobinhoodMcp
from .rank import rank_month
from .schedule import month_already_rebalanced, should_rebalance


def build_prompt(ranking: dict, day: date) -> str:
    basket = json.dumps(ranking["top"], indent=2)
    label = ranking["label"]
    window = ranking["window"]
    dry = "DRY_RUN is on. review_* is allowed. place_* and cancel_* will be blocked locally. Still walk the full decision path and write the report with dry_run true." if config.DRY_RUN else "DRY_RUN is off. Orders you place are real money in the agentic account."
    return f"""Call get_accounts now. Do not write a plan first.

Scheduled monthly rebalance for {day.isoformat()}.

{dry}

This is the same sleeve as the momentum playbook:
- Universe ranked below is the S&P 500 by the finished prior calendar month percent price change.
- Ranking label: {label}
- Window: {window}
- Basket: top 20, equal weight.
- Prefer the mapped 2x long single-stock ETF when a new fractional open is allowed, or when one whole share fits the dollar slot. Otherwise buy the underlying stock.
- Never buy an inverse or short product. Never substitute an index leveraged ETF (TQQQ, UPRO, SSO, and similar) for a missing single-stock vehicle.
- Hold nothing else. Sell the current equity book first, then rebuild.

Basket JSON:
{basket}

Do this, in order. Use only the agentic account (agentic_allowed true from get_accounts). The client will refuse any other account.

1. get_accounts. Remember the agentic account number.
2. get_equity_positions for that account. For each open equity with non-zero quantity, review_equity_order then place_equity_order: side=sell, type=market, market_hours=regular_hours, time_in_force=gfd, quantity=the exact position quantity as a string. Do not sell other asset classes.
3. get_portfolio for that account. Read buying power. Size each name as (buying_power - 1) / 20 dollars. Format dollar_amount as a 2-decimal string. Leave about $1 cash. If buying power is under $20, stop and write a report that says the account is too small. Do not invent a size.
4. For each of the 20 names, choose the vehicle:
   - If preferred_vehicle is non-empty, call get_equity_tradability (batches of at most 10 symbols) for that ETF and, if needed, the underlying.
   - Buy the ETF when fractional trading is allowed, or when one whole share's price fits the dollar slot.
   - Otherwise buy the underlying stock.
5. For each buy: review_equity_order, then place_equity_order. type=market, side=buy, dollar_amount set, market_hours=regular_hours, time_in_force=gfd. Pass exactly one of quantity or dollar_amount. Buys use dollar_amount.
6. Read the review payload. If it would reject the order (halt, not tradable, fractional not allowed, buying power), do not place it. Fall back from the ETF to the stock when that was the reason. If the stock is also blocked, skip the name and record why.
7. If review says the customer must approve in the Robinhood app and there is no blocking check, still call place_equity_order. That submits the approval request. Say so in the report. Do not claim a fill that the order tools did not return.
8. get_equity_orders and get_portfolio. Report only fills, statuses, and balances those tools returned.
9. write_run_report once.
   - data.date = "{day.isoformat()}"
   - data.kind = "monthly_rebalance"
   - data.ranking_label = "{label}"
   - data.window = "{window}"
   - data.basket = the 20 names with underlying, return_pct, symbol actually used, leveraged true/false, skip reason if any
   - data.orders = what review/place returned (status, shares, price) or the dry-run block
   - markdown = a short human report with the same facts

If a tool returns an error, quote it and stop. Do not invent fills, prices, or account numbers.
"""


def run_monthly(day: date | None = None, *, force: bool = False) -> int:
    day = day or date.today()
    ok, reason = should_rebalance(day)
    print(f"[gate] {day.isoformat()}: {reason}", flush=True)
    if not ok and not force:
        print("Skipping. The job runs every Monday; only the first open session of the month trades.", flush=True)
        return 0
    if not ok and force:
        print("[gate] --force set, continuing anyway.", flush=True)

    existing = month_already_rebalanced(config.RUNS_DIR, day)
    if existing and not config.DRY_RUN:
        print(f"Skipping. This month already has a live rebalance report: {existing}", flush=True)
        return 0
    if existing and config.DRY_RUN:
        print(f"[gate] dry-run report already exists ({existing}); running again because DRY_RUN=1.", flush=True)

    ranking = rank_month(day, complete_prior=True, top=20)
    prompt = build_prompt(ranking, day)
    with RobinhoodMcp() as mcp:
        result = Agent(
            mcp,
            tool_allowlist=config.MONTHLY_TOOLS,
            idempotency_day=day.isoformat(),
            enable_report=True,
        ).run(prompt)

    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    transcript = config.RUNS_DIR / f"{day.isoformat()}-transcript.json"
    transcript.write_text(json.dumps({
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": config.DRY_RUN,
        "model": config.OLLAMA_MODEL,
        "ranking": ranking,
        "final": result.text,
        "report": result.report,
        "trace": result.trace,
    }, indent=2) + "\n")
    print(f"Transcript → {transcript}", flush=True)
    return 0
