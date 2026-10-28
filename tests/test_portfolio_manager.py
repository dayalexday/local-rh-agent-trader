from local_rh_agent.providers.mock import MockProvider
from local_rh_agent.team.portfolio import analyze, build_orders
from local_rh_agent.team.risk import RiskPolicy, build_risk_decision


def _decision():
    rows = [
        {
            "rank": 1,
            "underlying": "NVDA",
            "return_pct": 12,
            "preferred_vehicle": "NVDU",
            "sector": "Technology",
            "sentiment_score": -1,
        },
        {
            "rank": 2,
            "underlying": "AAPL",
            "return_pct": 8,
            "preferred_vehicle": "AAPU",
            "sector": "Technology",
            "sentiment_score": 0,
        },
        {
            "rank": 3,
            "underlying": "XOM",
            "return_pct": 3,
            "preferred_vehicle": "XOMX",
            "sector": "Energy",
            "sentiment_score": None,
        },
    ]
    policy = RiskPolicy(max_name_weight=0.70, max_sector_weight=0.80, max_leveraged_weight=1)
    return build_risk_decision(rows, 10_000, policy)


def test_orders_cannot_resurrect_a_veto_or_raise_size():
    decision = _decision()
    orders = build_orders(decision)
    assert "NVDA" not in {order["underlying"] for order in orders}
    assert "ZZZZ" not in {order["symbol"] for order in orders}
    by_risk = {pos["underlying"]: pos for pos in decision["positions"]}
    for order in orders:
        allowed = by_risk[order["underlying"]]
        assert order["symbol"] == allowed["symbol"]
        assert float(order["dollar_amount"]) == pytest_dollars(allowed["illustrative_dollars"])
        assert order["weight"] <= allowed["weight"] + 1e-9


def pytest_dollars(value: float) -> float:
    return round(float(value), 2)


def test_model_commentary_is_not_copied_into_the_order_list():
    memo = analyze(_decision(), provider=MockProvider(), lessons=[], as_of="2026-11-02")
    assert "ZZZZ" in memo.facts["ignored_tickers"]
    assert all(order["symbol"] != "ZZZZ" for order in memo.facts["orders"])
    assert memo.facts["sell_book_first"] is True
    assert {order["underlying"] for order in memo.facts["orders"]} == {"AAPL", "XOM"}
