from local_rh_agent.providers.mock import MockProvider
from local_rh_agent.team.news import score_headlines
from local_rh_agent.team.sentiment import analyze, score_names


def _names():
    return [
        {"rank": 1, "underlying": "NVDA", "return_pct": 10, "preferred_vehicle": "NVDU", "sector": "Technology"},
        {"rank": 2, "underlying": "XOM", "return_pct": 4, "preferred_vehicle": "", "sector": "Energy"},
    ]


def test_lexicon_scores_and_missing_news_is_neutral():
    assert score_headlines([]) is None
    assert score_headlines(["Company reports quarterly results"]) == 0
    assert score_headlines(["NVDA faces fraud investigation and shareholder lawsuit"]) == -1
    assert score_headlines(["Analysts upgrade the stock after a record surge"]) == 1

    def headlines(symbol: str) -> list[str]:
        if symbol == "NVDA":
            return ["NVDA faces fraud investigation and shareholder lawsuit"]
        return []

    scored = score_names(_names(), headlines)
    assert scored[0]["sentiment_score"] == -1
    assert scored[1]["sentiment_score"] is None
    assert scored[1]["news_available"] is False


def test_headline_source_errors_do_not_drop_names():
    def boom(symbol: str) -> list[str]:
        raise RuntimeError("yfinance down")

    memo = analyze(_names(), provider=MockProvider(), lessons=[], as_of="2026-11-02", headline_source=boom)
    assert [row["underlying"] for row in memo.facts["names"]] == ["NVDA", "XOM"]
    assert memo.facts["unavailable"] == 2
    assert all(row["sentiment_score"] is None for row in memo.facts["names"])
