"""Run the research team, then the paper preview. Broker execution is opt-in."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .. import config
from ..memory import load_jsonl, record_decision
from ..providers import get_provider
from ..providers.base import ModelError
from ..reflection import grade
from . import momentum, portfolio, risk, sentiment
from .executor import execution_markdown, paper_execute, resolve_broker_dry_run
from .memo import write_memo
from .risk import policy_from_env, policy_with_memory

ROLES = ("momentum", "sentiment", "risk", "portfolio", "reflection")


class _DownProvider:
    """Stand-in used when the configured backend fails its probe. Memos still get written."""

    def __init__(self, inner: Any, reason: str):
        self.name = getattr(inner, "name", "unavailable")
        self.model = getattr(inner, "model", "")
        self.reason = reason

    def ensure_available(self) -> None:
        return None

    def complete(self, *, system: str, user: str, temperature: float | None = None) -> str:
        raise ModelError(self.reason)


def _providers(provider: Any | None, provider_name: str | None) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for role in ROLES:
        item = provider if provider is not None else get_provider(provider_name, role=role)
        resolved[role] = item
    checked: list[Any] = []
    for item in resolved.values():
        if any(item is seen for seen in checked):
            continue
        checked.append(item)
        try:
            item.ensure_available()
        except Exception as exc:
            print(f"[team] {getattr(item, 'name', 'model')} unavailable ({exc}). Commentary will be templated.", flush=True)
            down = _DownProvider(item, str(exc))
            for role, current in list(resolved.items()):
                if current is item:
                    resolved[role] = down
    return resolved


def run_team(
    *,
    as_of: date,
    ranking: dict[str, Any],
    buying_power: float,
    provider: Any | None = None,
    provider_name: str | None = None,
    headline_source: Callable[[str], list[str]] | None = None,
    execute: bool = False,
    dry_run: bool = True,
    output_dir: Path | None = None,
    lessons_path: Path | None = None,
    decisions_path: Path | None = None,
    quote: Callable | None = None,
    policy: risk.RiskPolicy | None = None,
) -> dict[str, Any]:
    """One research pass. `execute` is the only switch that constructs the broker client."""
    stamp = as_of.isoformat()
    lessons_path = lessons_path or config.LESSONS_PATH
    decisions_path = decisions_path or config.DECISIONS_PATH
    output_dir = output_dir or (config.RUNS_DIR / "team" / stamp)
    providers = _providers(provider, provider_name)

    reflection = grade(
        as_of=as_of,
        decisions_path=decisions_path,
        lessons_path=lessons_path,
        quote=quote,
        provider=providers["reflection"],
    )
    lessons = load_jsonl(lessons_path)
    base_policy = policy or policy_from_env()
    active_policy, memory_note = policy_with_memory(base_policy, lessons)

    print(f"[team] {stamp} provider={providers['momentum'].name} buying_power={buying_power:.2f}", flush=True)
    momentum_memo = momentum.analyze(ranking, provider=providers["momentum"], lessons=lessons, as_of=stamp)
    sentiment_memo = sentiment.analyze(
        momentum_memo.facts["top"],
        provider=providers["sentiment"],
        lessons=lessons,
        as_of=stamp,
        headline_source=headline_source,
    )
    risk_memo, decision = risk.analyze(
        sentiment_memo.facts["names"],
        buying_power=buying_power,
        policy=active_policy,
        provider=providers["risk"],
        lessons=lessons,
        as_of=stamp,
        memory_note=memory_note,
    )
    portfolio_memo = portfolio.analyze(decision, provider=providers["portfolio"], lessons=lessons, as_of=stamp)

    print(
        f"[team] momentum {len(momentum_memo.facts['top'])} names · "
        f"risk {decision['decision']} · "
        f"orders {len(portfolio_memo.facts['orders'])}",
        flush=True,
    )

    paper = paper_execute(portfolio_memo.facts)
    broker = None
    if execute:
        from .executor import execute_with_broker

        broker_dry = resolve_broker_dry_run(dry_run)
        print(
            f"[team] execute requested. Broker dry-run={'on' if broker_dry else 'OFF'}.",
            flush=True,
        )
        broker = execute_with_broker(portfolio_memo.facts, day=as_of, dry_run=broker_dry)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_memo(output_dir, momentum_memo, momentum.markdown_body(momentum_memo))
    write_memo(output_dir, sentiment_memo, sentiment.markdown_body(sentiment_memo))
    write_memo(output_dir, risk_memo, risk.markdown_body(risk_memo))
    write_memo(output_dir, portfolio_memo, portfolio.markdown_body(portfolio_memo))
    (output_dir / "execution.json").write_text(json.dumps({"paper": paper, "broker": broker}, indent=2, default=str) + "\n")
    (output_dir / "execution.md").write_text(execution_markdown(paper if broker is None else broker))
    (output_dir / "reflection.json").write_text(json.dumps(reflection, indent=2, default=str) + "\n")

    orders = portfolio_memo.facts["orders"]
    record_decision(decisions_path, {
        "as_of": stamp,
        "decision": decision["decision"],
        "buying_power": buying_power,
        "orders": [
            {
                "symbol": order["symbol"],
                "underlying": order["underlying"],
                "weight": order["weight"],
                "leveraged": order["leveraged"],
                "dollar_amount": order["dollar_amount"],
                "sector": order["sector"],
            }
            for order in orders
        ],
    })

    vetoes = [pos["underlying"] for pos in decision["positions"] if pos["action"] == "veto"]
    swaps = [pos["underlying"] for pos in decision["positions"] if "leverage cap" in str(pos.get("reason"))]
    manifest = {
        "as_of": stamp,
        "decision": decision["decision"],
        "execute": execute,
        "dry_run": True if not execute else resolve_broker_dry_run(dry_run),
        "broker_contacted": bool(broker and broker.get("broker_contacted")),
        "orders": orders,
        "vetoes": vetoes,
        "leverage_swaps": swaps,
        "cash_weight": decision["cash_weight"],
        "memory_note": memory_note,
        "reflection": {key: reflection[key] for key in ("wrote_lesson", "reason") if key in reflection},
        "providers": {role: f"{item.name}/{item.model}" for role, item in providers.items()},
        "output_dir": str(output_dir),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[team] wrote {output_dir}", flush=True)
    return manifest
