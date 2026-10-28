import pytest

from local_rh_agent.team.risk import (
    RiskPolicy,
    build_risk_decision,
    policy_with_memory,
    project_weights,
)


def _row(symbol, ret, sector, vehicle="", score=None, rank=1):
    return {
        "rank": rank,
        "underlying": symbol,
        "return_pct": ret,
        "preferred_vehicle": vehicle,
        "sector": sector,
        "sentiment_score": score,
    }


def test_project_weights_caps_a_single_sector_and_a_name():
    names = ["A", "B", "C", "D", "E"]
    sectors = {name: "Technology" for name in names}
    weights = project_weights(names, sectors, max_name=0.30, max_sector=0.40)
    assert sum(weights.values()) == pytest.approx(0.40, abs=1e-6)
    assert max(weights.values()) <= 0.30 + 1e-9

    spread = {name: sector for name, sector in zip("ABCDE", ["T", "F", "H", "E", "I"])}
    capped = project_weights(list(spread), spread, max_name=0.10, max_sector=0.40)
    assert sum(capped.values()) == pytest.approx(0.50, abs=1e-6)
    assert max(capped.values()) == pytest.approx(0.10, abs=1e-6)


def test_sentiment_veto_removes_the_name_and_renormalizes():
    rows = [
        _row("AAA", 10, "Technology", score=-1, rank=1),
        _row("BBB", 8, "Financials", rank=2),
        _row("CCC", 6, "Energy", rank=3),
        _row("DDD", 4, "Health Care", rank=4),
    ]
    policy = RiskPolicy(max_name_weight=0.50, max_sector_weight=0.50, max_leveraged_weight=1)
    decision = build_risk_decision(rows, 10_000, policy)
    by_name = {pos["underlying"]: pos for pos in decision["positions"]}
    assert by_name["AAA"]["action"] == "veto"
    assert by_name["AAA"]["weight"] == 0
    assert decision["decision"] == "resize"
    for symbol in ("BBB", "CCC", "DDD"):
        assert by_name[symbol]["weight"] == pytest.approx(1 / 3, abs=0.001)
        assert by_name[symbol]["action"] == "keep"


def test_leverage_cap_swaps_the_weakest_vehicles_first():
    rows = [
        _row("AAA", 4, "Technology", vehicle="AAAU", rank=1),
        _row("BBB", 3, "Financials", vehicle="BBBU", rank=2),
        _row("CCC", 2, "Energy", vehicle="CCCU", rank=3),
        _row("DDD", 1, "Health Care", vehicle="DDDU", rank=4),
    ]
    policy = RiskPolicy(max_name_weight=0.30, max_sector_weight=0.50, max_leveraged_weight=0.50)
    decision = build_risk_decision(rows, 10_000, policy)
    by_name = {pos["underlying"]: pos for pos in decision["positions"]}
    assert by_name["AAA"]["symbol"] == "AAAU"
    assert by_name["BBB"]["symbol"] == "BBBU"
    assert by_name["CCC"]["symbol"] == "CCC"
    assert by_name["DDD"]["symbol"] == "DDD"
    leveraged = sum(pos["weight"] for pos in decision["positions"] if pos["leveraged"])
    assert leveraged == pytest.approx(0.50, abs=1e-6)


def test_buying_power_below_the_minimum_vetoes_the_book():
    decision = build_risk_decision([_row("AAA", 1, "Energy")], 10, RiskPolicy())
    assert decision["decision"] == "veto"
    assert decision["positions"][0]["weight"] == 0
    assert "below" in decision["notes"][0]


def test_index_leveraged_etf_is_not_a_vehicle():
    decision = build_risk_decision(
        [_row("AAA", 5, "Technology", vehicle="TQQQ")],
        10_000,
        RiskPolicy(max_name_weight=1, max_sector_weight=1, max_leveraged_weight=1),
    )
    pos = decision["positions"][0]
    assert pos["symbol"] == "AAA"
    assert pos["leveraged"] is False
    assert any("TQQQ" in note for note in decision["notes"])


def test_memory_tightens_only_after_a_bad_leveraged_sleeve():
    tight, note = policy_with_memory(RiskPolicy(), [{"leveraged_return_pct": -9.0}])
    assert tight.max_leveraged_weight == 0.25
    assert note and "25%" in note
    unchanged, quiet = policy_with_memory(RiskPolicy(), [{"leveraged_return_pct": -1.0}])
    assert unchanged.max_leveraged_weight == 0.50
    assert quiet is None
    assert policy_with_memory(RiskPolicy(), [])[1] is None
