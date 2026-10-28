"""Risk manager. Weights, vetoes, and leverage swaps are pure Python."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .commentary import write_commentary
from .memo import Memo
from .momentum import INDEX_LEVERAGED_DENY

# A leveraged sleeve worse than this on the latest lesson tightens the next run.
LEVERAGE_LOSS_LIMIT_PCT = -8.0
TIGHT_LEVERAGE_CAP = 0.25


@dataclass(frozen=True)
class RiskPolicy:
    max_name_weight: float = 0.10
    max_sector_weight: float = 0.40
    max_leveraged_weight: float = 0.50
    sentiment_veto: float = -0.5
    min_buying_power: float = 20.0
    cash_buffer: float = 1.0

    def as_dict(self) -> dict[str, float]:
        return {
            "max_name_weight": self.max_name_weight,
            "max_sector_weight": self.max_sector_weight,
            "max_leveraged_weight": self.max_leveraged_weight,
            "sentiment_veto": self.sentiment_veto,
            "min_buying_power": self.min_buying_power,
            "cash_buffer": self.cash_buffer,
        }


def policy_from_env() -> RiskPolicy:
    from .. import config

    return RiskPolicy(
        max_name_weight=config.env_float("MAX_NAME_WEIGHT", 0.10),
        max_sector_weight=config.env_float("MAX_SECTOR_WEIGHT", 0.40),
        max_leveraged_weight=config.env_float("MAX_LEVERAGED_WEIGHT", 0.50),
        sentiment_veto=config.env_float("SENTIMENT_VETO", -0.5),
        min_buying_power=config.env_float("MIN_BUYING_POWER", 20.0),
        cash_buffer=config.env_float("CASH_BUFFER", 1.0),
    )


def policy_with_memory(policy: RiskPolicy, lessons: list[dict[str, Any]]) -> tuple[RiskPolicy, str | None]:
    """Tighten leverage for one run after a bad leveraged sleeve. Other lessons are prose only."""
    if not lessons:
        return policy, None
    leveraged = lessons[-1].get("leveraged_return_pct")
    if not isinstance(leveraged, (int, float)) or isinstance(leveraged, bool):
        return policy, None
    if float(leveraged) > LEVERAGE_LOSS_LIMIT_PCT:
        return policy, None
    tightened = min(policy.max_leveraged_weight, TIGHT_LEVERAGE_CAP)
    note = (
        f"Memory: the leveraged sleeve returned {float(leveraged):.1f}% "
        f"(limit {LEVERAGE_LOSS_LIMIT_PCT:.0f}%). "
        f"This run caps leveraged weight at {tightened:.0%}."
    )
    if tightened < policy.max_leveraged_weight - 1e-12:
        return replace(policy, max_leveraged_weight=tightened), note
    return policy, note


def project_weights(
    names: list[str],
    sector_of: dict[str, str],
    max_name: float,
    max_sector: float,
) -> dict[str, float]:
    """Equal-weight start, then name and sector caps. Leftover weight is cash."""
    if not names:
        return {}
    if max_name <= 0 or max_sector <= 0:
        return {name: 0.0 for name in names}
    weights = {name: 1.0 / len(names) for name in names}
    for _ in range(80):
        changed = False
        for name in names:
            if weights[name] > max_name + 1e-12:
                weights[name] = max_name
                changed = True
        totals = _sector_totals(weights, sector_of)
        for sector, total in totals.items():
            if total > max_sector + 1e-12:
                scale = max_sector / total
                for name in names:
                    if sector_of[name] == sector:
                        weights[name] *= scale
                changed = True
        if changed:
            continue
        cash = 1.0 - sum(weights.values())
        if cash <= 1e-8:
            break
        totals = _sector_totals(weights, sector_of)
        room = _room(names, weights, sector_of, totals, max_name, max_sector)
        if not room:
            break
        room_sum = sum(room.values())
        take = min(cash, room_sum)
        for name, space in room.items():
            weights[name] += take * (space / room_sum)
    for name in list(weights):
        if weights[name] < 1e-12:
            weights[name] = 0.0
    return weights


def _sector_totals(weights: dict[str, float], sector_of: dict[str, str]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for name, weight in weights.items():
        sector = sector_of[name]
        totals[sector] = totals.get(sector, 0.0) + weight
    return totals


def _room(
    names: list[str],
    weights: dict[str, float],
    sector_of: dict[str, str],
    totals: dict[str, float],
    max_name: float,
    max_sector: float,
) -> dict[str, float]:
    grouped: dict[str, list[str]] = {}
    for name in names:
        if max_name - weights[name] > 1e-12:
            grouped.setdefault(sector_of[name], []).append(name)
    room: dict[str, float] = {}
    for sector, group in grouped.items():
        sec_room = max_sector - totals.get(sector, 0.0)
        if sec_room <= 1e-12:
            continue
        name_rooms = {name: max_name - weights[name] for name in group}
        base = sum(name_rooms.values())
        if base <= 0:
            continue
        for name, name_room in name_rooms.items():
            room[name] = min(name_room, sec_room * (name_room / base))
    return room


def _round_weights(weights: dict[str, float]) -> dict[str, float]:
    rounded = {name: round(max(weight, 0.0), 4) for name, weight in weights.items()}
    total = round(sum(rounded.values()), 4)
    if total > 1:
        largest = max(rounded, key=rounded.get)
        rounded[largest] = round(rounded[largest] - (total - 1.0), 4)
    return rounded


def _dollars(weight: float, buying_power: float, cash_buffer: float) -> float:
    if weight <= 0:
        return 0.0
    deployable = buying_power - cash_buffer if buying_power >= 20 else buying_power
    return round(weight * max(deployable, 0.0), 2)


def _position(
    row: dict[str, Any],
    *,
    symbol: str,
    weight: float,
    dollars: float,
    action: str,
    reason: str,
    leveraged: bool,
) -> dict[str, Any]:
    return {
        "rank": row["rank"],
        "underlying": row["underlying"],
        "symbol": symbol,
        "leveraged": leveraged,
        "sector": row["sector"],
        "return_pct": row["return_pct"],
        "sentiment_score": row.get("sentiment_score"),
        "weight": round(float(weight), 4),
        "illustrative_dollars": round(float(dollars), 2),
        "action": action,
        "reason": reason,
    }


def build_risk_decision(
    candidates: list[dict[str, Any]],
    buying_power: float,
    policy: RiskPolicy,
    *,
    memory_note: str | None = None,
) -> dict[str, Any]:
    notes: list[str] = []
    if memory_note:
        notes.append(memory_note)

    prepared: list[dict[str, Any]] = []
    for row in candidates:
        underlying = str(row["underlying"]).upper()
        vehicle = str(row.get("preferred_vehicle") or "").upper()
        denied = False
        if vehicle in INDEX_LEVERAGED_DENY:
            notes.append(f"Ignored index product {vehicle} for {underlying}; using the stock.")
            vehicle = ""
            denied = True
        score = row.get("sentiment_score")
        if isinstance(score, bool):
            score = None
        prepared.append({
            "rank": int(row.get("rank") or 0),
            "underlying": underlying,
            "return_pct": float(row.get("return_pct") or 0),
            "preferred_vehicle": vehicle,
            "sector": str(row.get("sector") or "Unknown"),
            "sentiment_score": score,
            "denied_index_product": denied,
        })

    def finish(decision: str, positions: list[dict[str, Any]], cash: float) -> dict[str, Any]:
        return {
            "decision": decision,
            "buying_power": buying_power,
            "policy": policy.as_dict(),
            "memory_note": memory_note,
            "notes": notes,
            "cash_weight": round(max(cash, 0.0), 4),
            "positions": positions,
        }

    if buying_power < policy.min_buying_power:
        notes.append(
            f"Buying power {buying_power:.2f} is below {policy.min_buying_power:.0f}. No orders."
        )
        positions = [
            _position(
                row,
                symbol=row["preferred_vehicle"] or row["underlying"],
                weight=0.0,
                dollars=0.0,
                action="veto",
                reason="buying power too small",
                leveraged=bool(row["preferred_vehicle"]),
            )
            for row in prepared
        ]
        return finish("veto", positions, 1.0)

    vetoed = [
        row["underlying"]
        for row in prepared
        if isinstance(row["sentiment_score"], (int, float)) and float(row["sentiment_score"]) <= policy.sentiment_veto
    ]
    vetoed_set = set(vetoed)
    if vetoed:
        word = "name" if len(vetoed) == 1 else "names"
        notes.append(
            f"Vetoed {len(vetoed)} {word} with sentiment ≤ {policy.sentiment_veto}: {', '.join(vetoed)}."
        )

    active = [row for row in prepared if row["underlying"] not in vetoed_set]
    if not active:
        notes.append("Every name was vetoed. No orders.")
        positions = [
            _position(
                row,
                symbol=row["preferred_vehicle"] or row["underlying"],
                weight=0.0,
                dollars=0.0,
                action="veto",
                reason="sentiment veto",
                leveraged=bool(row["preferred_vehicle"]),
            )
            for row in prepared
        ]
        return finish("veto", positions, 1.0)

    sector_of = {row["underlying"]: row["sector"] for row in active}
    rounded = _round_weights(
        project_weights(
            [row["underlying"] for row in active],
            sector_of,
            policy.max_name_weight,
            policy.max_sector_weight,
        )
    )
    equal = 1.0 / len(active)
    choice = {row["underlying"]: row["preferred_vehicle"] for row in active}
    leveraged_weight = sum(rounded[row["underlying"]] for row in active if choice[row["underlying"]])
    swapped: list[str] = []
    if leveraged_weight > policy.max_leveraged_weight + 1e-9:
        for row in sorted(active, key=lambda item: (item["return_pct"], -item["rank"])):
            if leveraged_weight <= policy.max_leveraged_weight + 1e-9:
                break
            name = row["underlying"]
            if not choice[name] or rounded.get(name, 0.0) <= 0:
                continue
            leveraged_weight -= rounded[name]
            choice[name] = ""
            swapped.append(name)
        notes.append(
            "Swapped the weakest leveraged vehicles to the stock so leveraged weight "
            f"stays at or under {policy.max_leveraged_weight:.0%}: {', '.join(swapped)}."
        )

    cash = max(0.0, 1.0 - sum(rounded.values()))
    if cash > 0.001:
        notes.append(
            f"Cash {cash:.1%} after the name cap ({policy.max_name_weight:.0%}) "
            f"and sector cap ({policy.max_sector_weight:.0%})."
        )

    positions = []
    for row in prepared:
        name = row["underlying"]
        if name in vetoed_set:
            positions.append(_position(
                row,
                symbol=row["preferred_vehicle"] or name,
                weight=0.0,
                dollars=0.0,
                action="veto",
                reason="sentiment veto",
                leveraged=bool(row["preferred_vehicle"]),
            ))
            continue
        weight = rounded.get(name, 0.0)
        symbol = choice.get(name) or name
        leveraged = bool(choice.get(name))
        reasons: list[str] = []
        if row["denied_index_product"]:
            reasons.append("index product blocked")
        if name in swapped:
            reasons.append("leverage cap")
        if abs(weight - equal) > 0.0005:
            reasons.append("weight cap")
        if weight <= 0:
            action = "veto"
            reasons.append("zero weight")
        elif reasons:
            action = "resize"
        else:
            action = "keep"
        positions.append(_position(
            row,
            symbol=symbol,
            weight=weight,
            dollars=_dollars(weight, buying_power, policy.cash_buffer),
            action=action,
            reason=", ".join(reasons) if reasons else "within limits",
            leveraged=leveraged,
        ))

    if not any(pos["weight"] > 0 and pos["action"] != "veto" for pos in positions):
        label = "veto"
    elif any(pos["action"] != "keep" for pos in positions) or cash > 0.001:
        label = "resize"
    else:
        label = "approve"
    if label == "approve" and not any("Equal-weight" in note for note in notes):
        notes.append("Equal-weight book fits inside every cap.")
    return finish(label, positions, cash)


def _summary(decision: dict[str, Any]) -> str:
    active = [pos for pos in decision["positions"] if pos["weight"] > 0 and pos["action"] != "veto"]
    vetoes = [pos for pos in decision["positions"] if pos["action"] == "veto"]
    leveraged = sum(pos["weight"] for pos in active if pos["leveraged"])
    if decision["decision"] == "veto":
        lead = decision["notes"][0] if decision["notes"] else "No orders."
        return f"Veto. {lead}"
    return (
        f"{decision['decision'].capitalize()}: {len(active)} names, {len(vetoes)} vetoed, "
        f"leveraged weight {leveraged:.0%}, cash {decision['cash_weight']:.0%}."
    )


def _markdown(decision: dict[str, Any]) -> str:
    lines = ["### Audit", ""]
    for note in decision["notes"]:
        lines.append(f"- {note}")
    lines.extend([
        "",
        "| Underlying | Symbol | Action | Weight | Dollars | Reason |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ])
    for pos in decision["positions"]:
        lines.append(
            f"| {pos['underlying']} | {pos['symbol']} | {pos['action']} | "
            f"{pos['weight']:.1%} | {pos['illustrative_dollars']:.2f} | {pos['reason']} |"
        )
    return "\n".join(lines)


def analyze(
    candidates: list[dict[str, Any]],
    *,
    buying_power: float,
    policy: RiskPolicy,
    provider: Any,
    lessons: list[dict[str, Any]],
    as_of: str,
    memory_note: str | None = None,
) -> tuple[Memo, dict[str, Any]]:
    decision = build_risk_decision(candidates, buying_power, policy, memory_note=memory_note)
    prompt_rows = [
        {
            "underlying": pos["underlying"],
            "symbol": pos["symbol"],
            "action": pos["action"],
            "weight": pos["weight"],
            "reason": pos["reason"],
        }
        for pos in decision["positions"]
    ]
    known = {pos["underlying"] for pos in decision["positions"]} | {pos["symbol"] for pos in decision["positions"]}
    note = write_commentary(
        provider,
        role="risk",
        prompt_facts={"decision": decision["decision"], "notes": decision["notes"], "positions": prompt_rows},
        lessons=lessons,
        known_symbols=known,
    )
    facts = {
        **decision,
        "model_summary": note["model_summary"],
        "ignored_tickers": note["ignored_tickers"],
    }
    memo = Memo(
        agent="risk",
        as_of=as_of,
        title="Risk manager",
        summary=_summary(decision),
        reasoning=note["reasoning"],
        facts=facts,
        provider=getattr(provider, "name", ""),
        model=getattr(provider, "model", ""),
        commentary_source=note["source"],
    )
    return memo, decision


def markdown_body(memo: Memo) -> str:
    return _markdown(memo.facts)
