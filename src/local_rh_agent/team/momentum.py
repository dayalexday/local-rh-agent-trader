"""Momentum analyst. The top list is the ranking it was given; the model only comments."""
from __future__ import annotations

from typing import Any

from .commentary import write_commentary
from .memo import Memo
from .sectors import sector_for

# Index products the playbook forbids as a substitute for a single-stock vehicle.
INDEX_LEVERAGED_DENY = frozenset({
    "TQQQ", "SQQQ", "UPRO", "SPXU", "SPXL", "SSO", "SDS",
    "TNA", "TZA", "SOXL", "SOXS", "QLD", "QID",
    "TECL", "TECS", "FAS", "FAZ", "UDOW", "SDOW", "TMF", "TMV",
})


def locked_rows(ranking: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in ranking.get("top") or []:
        underlying = str(row["underlying"]).upper()
        vehicle = str(row.get("preferred_vehicle") or "").upper()
        if vehicle in INDEX_LEVERAGED_DENY:
            vehicle = ""
        rows.append({
            "rank": int(row.get("rank") or len(rows) + 1),
            "underlying": underlying,
            "return_pct": round(float(row.get("return_pct") or 0), 2),
            "preferred_vehicle": vehicle,
            "sector": str(row.get("sector") or sector_for(underlying)),
        })
    return rows


def _markdown(rows: list[dict[str, Any]], ranking: dict[str, Any]) -> str:
    lines = [
        f"Window `{ranking.get('window')}` · label `{ranking.get('label')}`.",
        "",
        "| Rank | Underlying | Prior month | Vehicle | Sector |",
        "| ---: | --- | ---: | --- | --- |",
    ]
    for row in rows:
        vehicle = row["preferred_vehicle"] or row["underlying"]
        lines.append(
            f"| {row['rank']} | {row['underlying']} | {row['return_pct']:.2f}% | {vehicle} | {row['sector']} |"
        )
    return "\n".join(lines)


def analyze(
    ranking: dict[str, Any],
    *,
    provider: Any,
    lessons: list[dict[str, Any]],
    as_of: str,
) -> Memo:
    rows = locked_rows(ranking)
    facts: dict[str, Any] = {
        "label": ranking.get("label"),
        "window": ranking.get("window"),
        "as_of": as_of,
        "source": "Deterministic ranking. The model cannot add or drop names.",
        "top": rows,
    }
    known = {row["underlying"] for row in rows} | {row["preferred_vehicle"] for row in rows if row["preferred_vehicle"]}
    note = write_commentary(
        provider,
        role="momentum",
        prompt_facts={"label": facts["label"], "window": facts["window"], "top": rows},
        lessons=lessons,
        known_symbols=known,
    )
    facts["model_summary"] = note["model_summary"]
    facts["ignored_tickers"] = note["ignored_tickers"]
    summary = f"Top {len(rows)} by finished prior-month return ({facts['label']})."
    return Memo(
        agent="momentum",
        as_of=as_of,
        title="Momentum analyst",
        summary=summary,
        reasoning=note["reasoning"],
        facts=facts,
        provider=getattr(provider, "name", ""),
        model=getattr(provider, "model", ""),
        commentary_source=note["source"],
    )


def markdown_body(memo: Memo) -> str:
    return _markdown(memo.facts["top"], memo.facts)
