"""CLI: auth, status, tools, run, monthly."""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

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


def cmd_run(prompt: str) -> int:
    with RobinhoodMcp() as mcp:
        Agent(mcp).run(prompt)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-rh-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="Browser OAuth login; save tokens locally")
    sub.add_parser("status", help="Ollama, token, MCP reachability, Monday gate")
    sub.add_parser("tools", help="Print tool names from Robinhood MCP")

    run_parser = sub.add_parser("run", help="One agent turn")
    run_parser.add_argument("prompt", nargs="+")

    monthly_parser = sub.add_parser("monthly", help="First-open-Monday momentum rebalance")
    monthly_parser.add_argument("--force", action="store_true", help="Skip the calendar gate")
    monthly_parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")

    rank_parser = sub.add_parser("rank", help="Print the prior-month top 20 and exit (no broker)")
    rank_parser.add_argument("--as-of", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "auth":
        login()
        return 0
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "tools":
        return cmd_tools()
    if args.cmd == "run":
        return cmd_run(" ".join(args.prompt))
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
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
