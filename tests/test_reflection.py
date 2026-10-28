from datetime import date

from local_rh_agent.memory import load_jsonl, record_decision
from local_rh_agent.providers.mock import MockProvider
from local_rh_agent.reflection import grade
from local_rh_agent.team.risk import RiskPolicy, policy_with_memory


def _decision(as_of: str) -> dict:
    return {
        "as_of": as_of,
        "decision": "resize",
        "buying_power": 10000,
        "orders": [
            {"symbol": "NVDU", "underlying": "NVDA", "weight": 0.5, "leveraged": True, "dollar_amount": "5000.00"},
            {"symbol": "XOM", "underlying": "XOM", "weight": 0.5, "leveraged": False, "dollar_amount": "5000.00"},
        ],
    }


def test_lesson_is_written_once_and_can_tighten_the_next_policy(tmp_path):
    decisions = tmp_path / "decisions.jsonl"
    lessons = tmp_path / "lessons.jsonl"
    record_decision(decisions, _decision("2026-10-05"))

    def quote(symbol, start, end):
        assert start == date(2026, 10, 5)
        assert end == date(2026, 11, 2)
        if symbol == "SPY":
            return 1.0
        if symbol == "NVDU":
            return -12.0
        return 2.0

    first = grade(
        as_of=date(2026, 11, 2),
        decisions_path=decisions,
        lessons_path=lessons,
        quote=quote,
        provider=MockProvider(),
    )
    assert first["wrote_lesson"] is True
    assert "capped leveraged" in first["lesson"]["lesson"].lower() or "caps leveraged" in first["lesson"]["lesson"]
    saved = load_jsonl(lessons)
    assert len(saved) == 1
    assert (tmp_path / "lessons.md").exists()

    again = grade(
        as_of=date(2026, 11, 2),
        decisions_path=decisions,
        lessons_path=lessons,
        quote=quote,
        provider=MockProvider(),
    )
    assert again["wrote_lesson"] is False
    assert again["reason"] == "already scored"
    assert len(load_jsonl(lessons)) == 1

    policy, note = policy_with_memory(RiskPolicy(), load_jsonl(lessons))
    assert policy.max_leveraged_weight == 0.25
    assert note


def test_missing_prices_do_not_invent_a_lesson(tmp_path):
    decisions = tmp_path / "decisions.jsonl"
    lessons = tmp_path / "lessons.jsonl"
    record_decision(decisions, _decision("2026-10-05"))
    result = grade(
        as_of=date(2026, 11, 2),
        decisions_path=decisions,
        lessons_path=lessons,
        quote=lambda symbol, start, end: None,
        provider=MockProvider(),
    )
    assert result == {"wrote_lesson": False, "reason": "prices unavailable"}
    assert not lessons.exists()


def test_no_prior_decision(tmp_path):
    result = grade(
        as_of=date(2026, 11, 2),
        decisions_path=tmp_path / "decisions.jsonl",
        lessons_path=tmp_path / "lessons.jsonl",
        quote=lambda symbol, start, end: 1.0,
        provider=MockProvider(),
    )
    assert result["reason"] == "no prior decision"
