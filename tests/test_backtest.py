import json
from datetime import date
from pathlib import Path

from local_rh_agent.backtest import run_backtest
from local_rh_agent.marketdata import load_price_csv, month_ends, rank_at, synthetic_panel


def test_backtest_is_deterministic_and_writes_a_chart(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("backtest tried to use the network")

    monkeypatch.setattr("yfinance.download", boom)
    monkeypatch.setattr("yfinance.Ticker", boom)

    first = run_backtest(output_dir=tmp_path / "a")
    second = run_backtest(output_dir=tmp_path / "b")
    assert first["team"] == second["team"]
    assert first["momentum"] == second["momentum"]
    assert first["spy"] == second["spy"]
    panel = synthetic_panel()
    assert first["months"] == len(panel.dates) - 2
    assert first["vetoes"] >= 1
    for book in ("team", "momentum", "spy"):
        assert first[book]["max_drawdown"] <= 0
        assert first[book]["total_return"] == first[book]["total_return"]
        assert first[book]["sharpe"] is not None
    chart = Path(first["chart"]).read_bytes()
    assert chart.startswith(b"\x89PNG")
    report = json.loads((tmp_path / "a" / "report.json").read_text())
    assert "Synthetic" in " ".join(report["notes"]) or any("synthetic" in note.lower() for note in report["notes"])
    assert (tmp_path / "a" / "report.md").is_file()


def test_price_csv_round_trip(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        "\n".join([
            "date,symbol,close,sector,vehicle",
            "2024-01-31,AAA,100,Technology,AAAU",
            "2024-01-31,AAAU,50,,",
            "2024-01-31,BBB,100,Energy,",
            "2024-01-31,SPY,400,,",
            "2024-02-29,AAA,110,Technology,AAAU",
            "2024-02-29,AAAU,70,,",
            "2024-02-29,BBB,90,Energy,",
            "2024-02-29,SPY,404,,",
            "2024-03-31,AAA,120,Technology,AAAU",
            "2024-03-31,AAAU,90,,",
            "2024-03-31,BBB,90,Energy,",
            "2024-03-31,SPY,408,,",
            "",
        ])
    )
    panel = load_price_csv(path)
    assert panel.universe == ["AAA", "BBB"]
    assert panel.vehicles["AAA"] == "AAAU"
    ranking = rank_at(panel, 1, 2)
    assert ranking["top"][0]["underlying"] == "AAA"
    assert ranking["top"][0]["preferred_vehicle"] == "AAAU"
    report = run_backtest(panel, top=1, output_dir=tmp_path / "bt", provider_name="mock")
    assert report["months"] == 1
    assert report["team"]["total_return"] != report["momentum"]["total_return"]


def test_month_ends_cover_leap_day():
    dates = month_ends(date(2024, 1, 31), 2)
    assert dates == [date(2024, 1, 31), date(2024, 2, 29)]
