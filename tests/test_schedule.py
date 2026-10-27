from datetime import date

from local_rh_agent.schedule import is_trading_day, scheduled_session, should_rebalance


def test_november_2026_is_the_first_monday():
    ok, _reason = should_rebalance(date(2026, 11, 2))
    assert ok
    assert scheduled_session(2026, 11) == date(2026, 11, 2)


def test_later_monday_does_not_trade():
    ok, reason = should_rebalance(date(2026, 11, 9))
    assert not ok
    assert "2026-11-02" in reason


def test_labor_day_2026_moves_to_tuesday():
    assert not is_trading_day(date(2026, 9, 7))
    assert scheduled_session(2026, 9) == date(2026, 9, 8)
    ok, _reason = should_rebalance(date(2026, 9, 8))
    assert ok
    closed, _reason = should_rebalance(date(2026, 9, 7))
    assert not closed


def test_weekend_skips():
    ok, _reason = should_rebalance(date(2026, 11, 1))
    assert not ok
