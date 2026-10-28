from local_rh_agent.providers.mock import MockProvider
from local_rh_agent.team.momentum import analyze


def _ranking(rows: int = 20) -> dict:
    top = []
    for index in range(1, rows + 1):
        top.append({
            "rank": index,
            "underlying": f"N{index:02d}",
            "return_pct": round(30 - index, 2),
            "preferred_vehicle": "TQQQ" if index == 1 else "",
        })
    return {
        "label": "2026-10",
        "as_of": "2026-11-02",
        "window": "2026-10-01 → 2026-10-31",
        "top": top,
    }


def test_top_list_is_the_ranking_not_the_model():
    memo = analyze(_ranking(), provider=MockProvider(), lessons=[], as_of="2026-11-02")
    assert [row["underlying"] for row in memo.facts["top"]] == [f"N{i:02d}" for i in range(1, 21)]
    assert memo.facts["top"][0]["preferred_vehicle"] == ""
    assert "ZZZZ" not in {row["underlying"] for row in memo.facts["top"]}
    assert "ZZZZ" in memo.facts["ignored_tickers"]
    assert memo.summary.startswith("Top 20")
    assert memo.commentary_source == "model"


def test_model_failure_still_locks_the_book():
    class Down:
        name = "down"
        model = "none"

        def complete(self, **kwargs):
            raise RuntimeError("offline")

    memo = analyze(_ranking(20), provider=Down(), lessons=[], as_of="2026-11-02")
    assert memo.commentary_source == "fallback"
    assert len(memo.facts["top"]) == 20
    assert memo.facts["top"][3]["underlying"] == "N04"
