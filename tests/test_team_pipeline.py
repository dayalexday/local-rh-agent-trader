import json
import subprocess
import sys
from datetime import date

import pytest

from local_rh_agent.__main__ import main
from local_rh_agent.providers.mock import MockProvider
from local_rh_agent.team.pipeline import run_team


def _ranking():
    specs = [
        ("NVDA", 20, "NVDU", "Technology", -1.0),
        ("AVGO", 15, "AVL", "Technology", None),
        ("MSFT", 14, "MSFU", "Technology", None),
        ("AAPL", 13, "AAPU", "Technology", None),
        ("AMD", 12, "AMUU", "Technology", None),
        ("MU", 11, "MUU", "Technology", 0.0),
        ("AMZN", 10, "AMZU", "Consumer Discretionary", None),
        ("XOM", 1, "XOMX", "Energy", None),
    ]
    top = []
    for rank, (symbol, ret, vehicle, sector, _score) in enumerate(specs, 1):
        top.append({
            "rank": rank,
            "underlying": symbol,
            "return_pct": ret,
            "preferred_vehicle": vehicle,
            "sector": sector,
        })
    return {
        "label": "2026-10",
        "as_of": "2026-11-02",
        "window": "2026-10-01 → 2026-10-31",
        "top": top,
        "scores": {symbol: score for symbol, _ret, _vehicle, _sector, score in specs},
    }


def _headlines(scores):
    def headlines(symbol: str) -> list[str]:
        if scores.get(symbol) == -1:
            return [f"{symbol} faces fraud investigation and shareholder lawsuit"]
        return []

    return headlines


def test_pipeline_respects_the_veto_and_the_caps(tmp_path):
    ranking = _ranking()
    manifest = run_team(
        as_of=date(2026, 11, 2),
        ranking=ranking,
        buying_power=10_000,
        provider=MockProvider(),
        headline_source=_headlines(ranking["scores"]),
        execute=False,
        output_dir=tmp_path / "run",
        lessons_path=tmp_path / "lessons.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        quote=lambda symbol, start, end: None,
    )
    orders = manifest["orders"]
    assert manifest["broker_contacted"] is False
    assert "NVDA" in manifest["vetoes"]
    assert "NVDA" not in {order["underlying"] for order in orders}
    assert "ZZZZ" not in {order["symbol"] for order in orders}
    assert max(order["weight"] for order in orders) <= 0.10 + 1e-9
    tech = sum(order["weight"] for order in orders if order["sector"] == "Technology")
    assert tech <= 0.40 + 1e-6
    leveraged = sum(order["weight"] for order in orders if order["leveraged"])
    assert leveraged <= 0.50 + 1e-6
    xom = next(order for order in orders if order["underlying"] == "XOM")
    assert xom["symbol"] == "XOM"
    assert xom["leveraged"] is False

    for name in ("momentum", "sentiment", "risk", "portfolio"):
        assert (tmp_path / "run" / f"{name}.json").is_file()
        assert (tmp_path / "run" / f"{name}.md").is_file()
    execution = json.loads((tmp_path / "run" / "execution.json").read_text())
    assert execution["broker"] is None
    assert execution["paper"]["orders"][0]["dry_run_place"]["blocked"] is True
    memo = json.loads((tmp_path / "run" / "momentum.json").read_text())
    assert [row["underlying"] for row in memo["facts"]["top"]][0] == "NVDA"


def test_second_run_reads_the_lesson_and_tightens_leverage(tmp_path):
    ranking = _ranking()
    common = dict(
        ranking=ranking,
        buying_power=10_000,
        provider=MockProvider(),
        headline_source=_headlines(ranking["scores"]),
        execute=False,
        lessons_path=tmp_path / "lessons.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        quote=lambda symbol, start, end: -20.0 if symbol != "SPY" else 1.0,
    )
    first = run_team(as_of=date(2026, 10, 31), output_dir=tmp_path / "oct", **common)
    first_lev = sum(order["weight"] for order in first["orders"] if order["leveraged"])
    assert first_lev == pytest.approx(0.50, abs=1e-6)
    assert first["memory_note"] is None

    second = run_team(as_of=date(2026, 11, 28), output_dir=tmp_path / "nov", **common)
    assert second["memory_note"]
    second_lev = sum(order["weight"] for order in second["orders"] if order["leveraged"])
    assert second_lev <= 0.25 + 1e-6
    lesson = (tmp_path / "lessons.jsonl").read_text()
    assert "2026-10-31" in lesson


def test_reflection_can_be_imported_before_the_pipeline():
    """The CLI imports reflection first. That order used to cycle through team/__init__."""
    code = (
        "from local_rh_agent.reflection import grade\n"
        "from local_rh_agent.team.pipeline import run_team\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cli_offline_dry_run_stays_on_the_machine(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("broker client was constructed for a paper run")

    monkeypatch.setattr("local_rh_agent.team.executor.execute_with_broker", boom)
    monkeypatch.setattr("local_rh_agent.mcp_client.RobinhoodMcp._post", boom)
    code = main([
        "team",
        "--dry-run",
        "--offline",
        "--provider",
        "mock",
        "--buying-power",
        "10000",
        "--output",
        str(tmp_path / "out"),
        "--memory",
        str(tmp_path / "memory"),
    ])
    assert code == 0
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["broker_contacted"] is False
    assert manifest["execute"] is False
    assert manifest["vetoes"]
    execution = json.loads((tmp_path / "out" / "execution.json").read_text())
    assert execution["paper"]["dry_run"] is True
    assert all(row["dry_run_place"]["blocked"] for row in execution["paper"]["orders"])
