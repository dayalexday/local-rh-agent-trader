"""The only team module that may construct a Robinhood client.

Research agents never import this. Paper mode forces dry-run on the client, so
`place_*` is dropped before any socket is opened. Live orders still require
DRY_RUN=0 in the environment; this module will not override that.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from .. import config
from ..agent import Agent, AgentResult, order_guard
from ..mcp_client import RobinhoodMcp

PAPER_ACCOUNT = "paper-agentic"


def resolve_broker_dry_run(cli_dry_run: bool) -> bool:
    """Dry when the CLI asks for it or the environment still has DRY_RUN on."""
    return bool(cli_dry_run or config.DRY_RUN)


def paper_execute(proposal: dict[str, Any], *, agentic_account: str = PAPER_ACCOUNT) -> dict[str, Any]:
    """Show the guards without contacting Robinhood.

    `RobinhoodMcp.call_tool` returns before HTTP when dry_run is true and the
    tool name is mutating. Review tools are not called here, because those
    would open a connection.
    """
    client = RobinhoodMcp(dry_run=True)
    previews: list[dict[str, Any]] = []
    for order in proposal.get("orders") or []:
        arguments = {
            "account_number": agentic_account,
            "symbol": order["symbol"],
            "side": "buy",
            "type": "market",
            "dollar_amount": order["dollar_amount"],
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
        }
        blocked = client.call_tool("place_equity_order", arguments)
        previews.append({
            "symbol": order["symbol"],
            "underlying": order.get("underlying"),
            "dollar_amount": order["dollar_amount"],
            "weight": order.get("weight"),
            "guard_with_agentic_account": order_guard("place_equity_order", arguments, {agentic_account}),
            "guard_before_get_accounts": order_guard("place_equity_order", arguments, set()),
            "guard_other_account": order_guard(
                "place_equity_order",
                {**arguments, "account_number": "not-agentic"},
                {agentic_account},
            ),
            "dry_run_place": blocked,
        })
    return {
        "mode": "paper",
        "broker_contacted": False,
        "dry_run": True,
        "orders": previews,
        "message": (
            "Robinhood was not contacted. place_equity_order was handed to the dry-run "
            "client, which drops mutating tools before a request is sent. "
            "The account guard still refuses a missing or non-agentic account."
        ),
    }


def build_execution_prompt(proposal: dict[str, Any], day: date, *, dry_run: bool) -> str:
    orders = proposal.get("orders") or []
    mode = (
        "DRY_RUN is on. review_* may run. place_* and cancel_* are blocked on this machine."
        if dry_run
        else "DRY_RUN is off. Orders you place are real money in the agentic account."
    )
    payload = json.dumps(orders, indent=2)
    if not orders:
        return f"""Call get_accounts. Do not place or review any order.

Team run for {day.isoformat()}. Risk vetoed the book. Do not sell and do not buy.
{mode}
Write a short summary that the account was not traded. Do not invent fills.
"""
    return f"""Call get_accounts now. Do not write a plan first.

Team execution for {day.isoformat()}.

{mode}

Trade only the account where agentic_allowed is true. The client refuses every other account.

The research team already chose the book. You may not add symbols, drop the risk vetoes, or raise a weight.
Illustrative dollar amounts below use a paper buying power. On a live account, size each name as
weight × (buying_power - 1) and format dollar_amount as a 2-decimal string. If buying power is under $20, stop and buy nothing.

Approved buys (JSON):
{payload}

Do this, in order:
1. get_accounts. Remember the agentic account number.
2. get_equity_positions for that account. Sell each open equity with non-zero quantity: review_equity_order then place_equity_order, side=sell, type=market, market_hours=regular_hours, time_in_force=gfd, quantity as a string. Do not sell other asset classes.
3. get_portfolio. Read buying power.
4. For each approved buy only: review_equity_order, then place_equity_order. type=market, side=buy, dollar_amount set, market_hours=regular_hours, time_in_force=gfd. Pass exactly one of quantity or dollar_amount.
5. If review would reject the order, do not place it. Record why. Do not substitute a symbol that is not in the JSON.
6. get_equity_orders. Report only statuses those tools returned. data.kind must be "team_execution", not "monthly_rebalance".

If a tool errors, quote it and stop. Do not invent fills, prices, or account numbers.
"""


def execute_with_broker(
    proposal: dict[str, Any],
    *,
    day: date,
    dry_run: bool,
) -> dict[str, Any]:
    """Hand the book to the existing guarded agent. DRY_RUN=1 cannot be turned off here."""
    if config.DRY_RUN:
        dry_run = True
    prompt = build_execution_prompt(proposal, day, dry_run=dry_run)
    with RobinhoodMcp(dry_run=dry_run) as mcp:
        result: AgentResult = Agent(
            mcp,
            tool_allowlist=config.MONTHLY_TOOLS,
            idempotency_day=day.isoformat(),
            enable_report=False,
        ).run(prompt)
    return {
        "mode": "broker",
        "broker_contacted": True,
        "dry_run": dry_run,
        "final": result.text,
        "trace": result.trace,
    }


def _guard_cell(message: str | None) -> str:
    return "pass" if not message else "refused"


def execution_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Execution ({report.get('mode')})",
        "",
        report.get("message") or "",
        "",
        f"Broker contacted: {report.get('broker_contacted')}. Dry-run: {report.get('dry_run')}.",
        "",
    ]
    if report.get("mode") == "paper":
        lines.extend([
            "Full refusal strings are in `execution.json`. The table is the pass/fail view.",
            "",
            "| Symbol | Agentic account | Before get_accounts | Other account | place_* |",
            "| --- | --- | --- | --- | --- |",
        ])
        sample_missing = ""
        sample_other = ""
        for row in report.get("orders") or []:
            place = row.get("dry_run_place") or {}
            sample_missing = sample_missing or str(row.get("guard_before_get_accounts") or "")
            sample_other = sample_other or str(row.get("guard_other_account") or "")
            lines.append(
                f"| {row['symbol']} | {_guard_cell(row.get('guard_with_agentic_account'))} | "
                f"{_guard_cell(row.get('guard_before_get_accounts'))} | "
                f"{_guard_cell(row.get('guard_other_account'))} | "
                f"{'blocked' if place.get('blocked') else 'sent'} |"
            )
        if sample_missing or sample_other:
            lines.extend(["", "Sample refusals:", ""])
            if sample_missing:
                lines.append(f"- Before get_accounts: {sample_missing}")
            if sample_other:
                lines.append(f"- Other account: {sample_other}")
    elif report.get("final"):
        lines.extend(["## Executor text", "", str(report["final"])])
    return "\n".join(lines).rstrip() + "\n"
