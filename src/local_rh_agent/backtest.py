"""Replay month-ends through the same team code the CLI uses.

The default panel is synthetic and the default model is the mock provider, so
this runs in CI without market data or an API key. Metrics are a research
harness, not a claim of an edge.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any

from . import config
from .marketdata import (
    PricePanel,
    headlines_for_ranking,
    load_price_csv,
    panel_return_pct,
    rank_at,
    synthetic_panel,
)
from .providers import get_provider
from .team.pipeline import run_team
from .team.risk import RiskPolicy

NOTES = [
    "Default prices are synthetic (seed 7), not a historical market replay and not evidence of an edge.",
    "Leveraged vehicles in the synthetic panel compound at twice the underlying's monthly return. Real 2x ETFs reset daily; volatility decay is not modeled.",
    "March and September of the offline fixture attach severe negative headlines to the top momentum name so the sentiment veto is exercised. Those headlines are not news.",
    "Sharpe uses monthly returns, annualized with sqrt(12), and a risk-free rate of 0. No costs, slippage, or taxes.",
    "Plain momentum is equal-weight the same top list in the underlyings, with no leverage, veto, or caps.",
    "The default universe is 18 names and the harness buys the top 8. Equal weight is 12.5%, above the 10% name cap, so the team holds residual cash. A live book is the top 20, where equal weight is 5% and that cap does not bind by itself.",
]


def max_drawdown(equity: list[float]) -> float:
    peak = equity[0] if equity else 1.0
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def sharpe_ratio(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    if var <= 0:
        return None
    return (mean / math.sqrt(var)) * math.sqrt(12)


def summarize(equity: list[float], returns: list[float]) -> dict[str, float | None]:
    total = equity[-1] / equity[0] - 1.0 if equity and equity[0] else 0.0
    sharpe = sharpe_ratio(returns)
    return {
        "total_return": round(total, 6),
        "max_drawdown": round(max_drawdown(equity), 6),
        "sharpe": None if sharpe is None else round(sharpe, 4),
    }


def _book_return_pct(orders: list[dict[str, Any]], panel: PricePanel, start: date, end: date) -> float:
    acc = 0.0
    for order in orders:
        returned = panel_return_pct(panel, str(order["symbol"]), start, end)
        if returned is None:
            raise RuntimeError(f"No price for {order['symbol']} between {start} and {end}")
        acc += float(order["weight"]) * float(returned)
    return acc


def _plain_orders(ranking: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(ranking.get("top") or [])
    if not rows:
        return []
    weight = 1.0 / len(rows)
    return [
        {
            "symbol": row["underlying"],
            "underlying": row["underlying"],
            "weight": weight,
            "leveraged": False,
        }
        for row in rows
    ]


def run_backtest(
    panel: PricePanel | None = None,
    *,
    top: int = 8,
    buying_power: float = 10000.0,
    provider_name: str = "mock",
    output_dir: Path,
    policy: RiskPolicy | None = None,
) -> dict[str, Any]:
    panel = panel or synthetic_panel()
    provider = get_provider(provider_name, role="team")
    output_dir.mkdir(parents=True, exist_ok=True)
    memory_dir = output_dir / "memory"
    lessons_path = memory_dir / "lessons.jsonl"
    decisions_path = memory_dir / "decisions.jsonl"

    def quote(symbol: str, start: date, end: date) -> float | None:
        return panel_return_pct(panel, symbol, start, end)

    dates = panel.dates
    curve_dates = [dates[1]]
    equity = {"team": [1.0], "momentum": [1.0], "spy": [1.0]}
    period_returns: dict[str, list[float]] = {"team": [], "momentum": [], "spy": []}
    vetoes = 0
    swaps = 0
    months_with_cash = 0

    for index in range(1, len(dates) - 1):
        as_of = dates[index]
        ranking = rank_at(panel, index, top)
        manifest = run_team(
            as_of=as_of,
            ranking=ranking,
            buying_power=buying_power,
            provider=provider,
            headline_source=headlines_for_ranking(ranking, as_of),
            execute=False,
            dry_run=True,
            output_dir=output_dir / "months" / as_of.isoformat(),
            lessons_path=lessons_path,
            decisions_path=decisions_path,
            quote=quote,
            policy=policy,
        )
        nxt = dates[index + 1]
        team_pct = _book_return_pct(manifest["orders"], panel, as_of, nxt)
        mom_pct = _book_return_pct(_plain_orders(ranking), panel, as_of, nxt)
        spy_pct = panel_return_pct(panel, "SPY", as_of, nxt)
        if spy_pct is None:
            raise RuntimeError("synthetic panel is missing SPY")
        for key, pct in ("team", team_pct), ("momentum", mom_pct), ("spy", spy_pct):
            fraction = float(pct) / 100.0
            period_returns[key].append(fraction)
            equity[key].append(equity[key][-1] * (1.0 + fraction))
        curve_dates.append(nxt)
        vetoes += len(manifest["vetoes"])
        swaps += len(manifest["leverage_swaps"])
        if float(manifest["cash_weight"]) > 0.001:
            months_with_cash += 1

    report = {
        "months": len(period_returns["team"]),
        "top": top,
        "provider": provider_name,
        "team": summarize(equity["team"], period_returns["team"]),
        "momentum": summarize(equity["momentum"], period_returns["momentum"]),
        "spy": summarize(equity["spy"], period_returns["spy"]),
        "vetoes": vetoes,
        "leverage_swaps": swaps,
        "months_with_cash": months_with_cash,
        "curve": [
            {
                "date": stamp.isoformat(),
                "team": round(equity["team"][i], 6),
                "momentum": round(equity["momentum"][i], 6),
                "spy": round(equity["spy"][i], 6),
            }
            for i, stamp in enumerate(curve_dates)
        ],
        "notes": NOTES,
    }
    chart = output_dir / "equity.png"
    _save_chart(chart, curve_dates, equity)
    report["chart"] = str(chart)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (output_dir / "report.md").write_text(_markdown(report))
    print(
        "[backtest] "
        f"team {report['team']['total_return']:.1%} · "
        f"momentum {report['momentum']['total_return']:.1%} · "
        f"SPY {report['spy']['total_return']:.1%} · "
        f"chart {chart}",
        flush=True,
    )
    return report


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Backtest",
        "",
        "| Book | Total return | Max drawdown | Sharpe |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key, label in ("team", "Team"), ("momentum", "Plain momentum"), ("spy", "SPY buy and hold"):
        row = report[key]
        sharpe = "n/a" if row["sharpe"] is None else f"{row['sharpe']:.2f}"
        lines.append(
            f"| {label} | {row['total_return']:.1%} | {row['max_drawdown']:.1%} | {sharpe} |"
        )
    lines.extend([
        "",
        f"Months: {report['months']}. Sentiment vetoes: {report['vetoes']}. "
        f"Leverage swaps: {report['leverage_swaps']}. Months with cash: {report['months_with_cash']}.",
        "",
        "## Notes",
        "",
    ])
    for note in report["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def _save_chart(path: Path, dates: list[date], equity: dict[str, list[float]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9.0, 4.8))
    labels = {"team": "Team", "momentum": "Plain momentum", "spy": "SPY"}
    for key in ("team", "momentum", "spy"):
        ax.plot(dates, equity[key], label=labels[key], linewidth=2)
    ax.set_title("Backtest equity — growth of $1 (synthetic unless you passed --prices)")
    ax.set_ylabel("Growth of $1")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def run_backtest_command(args: Any) -> int:
    panel = load_price_csv(Path(args.prices)) if args.prices else synthetic_panel()
    output = Path(args.output) if args.output else config.RUNS_DIR / "backtest"
    run_backtest(
        panel,
        top=args.top,
        buying_power=args.buying_power,
        provider_name=args.provider or "mock",
        output_dir=output,
    )
    return 0
