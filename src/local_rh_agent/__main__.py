"""CLI: auth, status, tools, run, monthly, team, backtest, reflect."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

from . import config
from .agent import Agent, check_ollama
from .mcp_client import RobinhoodMcp
from .oauth import login, token_status
from .schedule import should_rebalance


def _probe_mcp() -> str:
    try:
        response = httpx.post(
            config.RH_MCP_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            timeout=20,
        )
    except Exception as exc:
        return f"unreachable: {exc}"
    if response.status_code == 401:
        return "reachable, OAuth required (expected before auth)"
    return f"HTTP {response.status_code}: {response.text[:200]}"


def cmd_status() -> int:
    ok, detail = check_ollama()
    print(f"Ollama model {config.OLLAMA_MODEL}: {'ok' if ok else detail}")
    print(f"Team provider: {config.provider_name()}")
    print(f"DRY_RUN: {'on' if config.DRY_RUN else 'OFF — live orders enabled'}")
    print(f"Robinhood token: {token_status()}")
    print(f"MCP endpoint: {_probe_mcp()}")
    today = date.today()
    allowed, reason = should_rebalance(today)
    print(f"Today {today.isoformat()}: {'would rebalance' if allowed else 'would skip'} ({reason})")
    print(f"Project: {config.ROOT}")
    return 0 if ok else 1


def cmd_tools() -> int:
    with RobinhoodMcp() as mcp:
        mcp.initialize()
        tools = mcp.list_tools()
    print(f"{len(tools)} tools")
    for tool in tools:
        print(tool.get("name", "?"))
    return 0


def cmd_run(prompt: str, *, all_tools: bool) -> int:
    allowlist = None if all_tools else config.MONTHLY_TOOLS
    with RobinhoodMcp() as mcp:
        Agent(mcp, tool_allowlist=allowlist).run(prompt)
    return 0


def _parse_day(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def cmd_team(args: argparse.Namespace) -> int:
    from .marketdata import headlines_for_ranking, offline_as_of_index, panel_return_pct, rank_at, synthetic_panel
    from .reflection import yfinance_quote
    from .team.executor import resolve_broker_dry_run
    from .team.pipeline import run_team

    buying_power = args.buying_power if args.buying_power is not None else config.env_float("PAPER_BUYING_POWER", 10000.0)
    if buying_power <= 0:
        print("buying power must be positive", flush=True)
        return 2

    panel = synthetic_panel() if args.offline else None
    quote = yfinance_quote
    headline_source = None
    if args.ranking:
        ranking = json.loads(Path(args.ranking).read_text())
        as_of = _parse_day(args.as_of) if args.as_of else date.fromisoformat(str(ranking["as_of"]))
    elif args.offline:
        assert panel is not None
        ranking = rank_at(panel, offline_as_of_index(panel), 20)
        as_of = _parse_day(args.as_of) if args.as_of else date.fromisoformat(str(ranking["as_of"]))
    else:
        from .rank import rank_month

        as_of = _parse_day(args.as_of)
        ranking = rank_month(as_of, complete_prior=True, top=20)

    if args.offline:
        headline_source = headlines_for_ranking(ranking, as_of)
        if panel is not None and not args.ranking:
            quote = lambda symbol, start, end: panel_return_pct(panel, symbol, start, end)
        else:
            quote = lambda symbol, start, end: None

    output = Path(args.output) if args.output else config.RUNS_DIR / "team" / as_of.isoformat()
    memory = Path(args.memory) if args.memory else config.MEMORY_DIR
    dry_run = resolve_broker_dry_run(args.dry_run or not args.execute)
    if not args.execute:
        print("[team] paper preview only. Pass --execute to hand the book to the guarded broker client.", flush=True)
    elif dry_run:
        print("[team] --execute with dry-run. place_* will be blocked before it leaves the machine.", flush=True)
    else:
        print("[team] --execute and DRY_RUN is off. Orders can be sent to the agentic account.", flush=True)

    run_team(
        as_of=as_of,
        ranking=ranking,
        buying_power=buying_power,
        provider_name=args.provider,
        headline_source=headline_source,
        execute=args.execute,
        dry_run=dry_run,
        output_dir=output,
        lessons_path=memory / "lessons.jsonl",
        decisions_path=memory / "decisions.jsonl",
        quote=quote,
    )
    return 0


def cmd_reflect(as_of: date) -> int:
    from .providers import get_provider
    from .providers.base import ModelError
    from .providers.mock import MockProvider
    from .reflection import grade, yfinance_quote

    provider = get_provider(role="reflection")
    try:
        provider.ensure_available()
    except (ModelError, Exception) as exc:
        print(f"[reflect] model unavailable ({exc}). The lesson text stays deterministic.", flush=True)
        provider = MockProvider()
    result = grade(
        as_of=as_of,
        decisions_path=config.DECISIONS_PATH,
        lessons_path=config.LESSONS_PATH,
        quote=yfinance_quote,
        provider=provider,
    )
    if result.get("wrote_lesson"):
        print(result["lesson"]["lesson"], flush=True)
    else:
        print(f"[reflect] {result.get('reason')}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-rh-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="Browser OAuth login; save tokens locally")
    sub.add_parser("status", help="Ollama, token, MCP reachability, Monday gate")
    sub.add_parser("tools", help="Print tool names from Robinhood MCP")

    run_parser = sub.add_parser("run", help="One agent turn")
    run_parser.add_argument("prompt", nargs="+")
    run_parser.add_argument(
        "--all-tools",
        action="store_true",
        help="Expose every Robinhood MCP tool (slow on 8GB). Default is a short equity set.",
    )

    monthly_parser = sub.add_parser("monthly", help="First-open-Monday momentum rebalance")
    monthly_parser.add_argument("--force", action="store_true", help="Skip the calendar gate")
    monthly_parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")

    rank_parser = sub.add_parser("rank", help="Print the prior-month top 20 and exit (no broker)")
    rank_parser.add_argument("--as-of", default=None)

    team_parser = sub.add_parser("team", help="Research team memos and a paper order list")
    team_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run. This is the default. There is no flag that turns DRY_RUN off.",
    )
    team_parser.add_argument(
        "--execute",
        action="store_true",
        help="Hand the book to the guarded Robinhood executor. Still honors DRY_RUN.",
    )
    team_parser.add_argument("--offline", action="store_true", help="Synthetic ranking and headlines. No market data and no broker.")
    team_parser.add_argument("--provider", default=None, help="ollama, openai, or mock. Overrides MODEL_PROVIDER for this run.")
    team_parser.add_argument("--as-of", default=None, help="YYYY-MM-DD")
    team_parser.add_argument("--buying-power", type=float, default=None, help="Paper buying power for illustrative dollars.")
    team_parser.add_argument("--ranking", default=None, help="Ranking JSON from a previous rank. Skips the download.")
    team_parser.add_argument("--output", default=None, help="Directory for memos. Default: runs/team/YYYY-MM-DD/")
    team_parser.add_argument("--memory", default=None, help="Directory for lessons.jsonl. Default: memory/")

    backtest_parser = sub.add_parser("backtest", help="Replay months through the team and write a report plus chart")
    backtest_parser.add_argument("--offline", action="store_true", help="Use the seeded synthetic panel (this is the default).")
    backtest_parser.add_argument("--prices", default=None, help="CSV of month-end closes. Columns: date,symbol,close,sector,vehicle.")
    backtest_parser.add_argument("--provider", default=None, help="Defaults to mock so the harness stays offline.")
    backtest_parser.add_argument("--top", type=int, default=8)
    backtest_parser.add_argument("--buying-power", type=float, default=10000.0)
    backtest_parser.add_argument("--output", default=None, help="Directory for report.json, report.md, and equity.png.")

    reflect_parser = sub.add_parser("reflect", help="Score the previous team decision and append a lesson")
    reflect_parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")

    args = parser.parse_args(argv)

    if args.cmd == "auth":
        login()
        return 0
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "tools":
        return cmd_tools()
    if args.cmd == "run":
        return cmd_run(" ".join(args.prompt), all_tools=args.all_tools)
    if args.cmd == "rank":
        from .rank import rank_month

        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        rank_month(as_of, complete_prior=True, top=20)
        return 0
    if args.cmd == "monthly":
        from .monthly import run_monthly

        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        if args.as_of:
            print(f"[as-of] {as_of.isoformat()} (wall clock {datetime.now().isoformat(timespec='seconds')})", flush=True)
        return run_monthly(as_of, force=args.force)
    if args.cmd == "team":
        return cmd_team(args)
    if args.cmd == "backtest":
        from .backtest import run_backtest_command

        return run_backtest_command(args)
    if args.cmd == "reflect":
        return cmd_reflect(_parse_day(args.as_of))
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
