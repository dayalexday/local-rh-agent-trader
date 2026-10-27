"""First-open-Monday gate. Same rule as the Cursor momentum automation."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

# NYSE full-day closures. Early closes still count as open sessions.
# Update when a new year rolls in.
NYSE_HOLIDAYS = {
    # 2026
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
    # 2027
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 3, 26),
    date(2027, 5, 31),
    date(2027, 6, 18),
    date(2027, 7, 5),
    date(2027, 9, 6),
    date(2027, 11, 25),
    date(2027, 12, 24),
    # 2028 (New Year's Day falls on Saturday and is observed Friday 2027-12-31)
    date(2027, 12, 31),
    date(2028, 1, 17),
    date(2028, 2, 21),
    date(2028, 4, 14),
    date(2028, 5, 29),
    date(2028, 6, 19),
    date(2028, 7, 4),
    date(2028, 9, 4),
    date(2028, 11, 23),
    date(2028, 12, 25),
}


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in NYSE_HOLIDAYS


def first_monday(year: int, month: int) -> date:
    day = date(year, month, 1)
    while day.weekday() != 0:
        day += timedelta(days=1)
    return day


def scheduled_session(year: int, month: int) -> date | None:
    """First Monday, or the next open session that same week if Monday is closed."""
    monday = first_monday(year, month)
    if is_trading_day(monday):
        return monday
    friday = monday + timedelta(days=4)
    day = monday + timedelta(days=1)
    while day <= friday:
        if is_trading_day(day):
            return day
        day += timedelta(days=1)
    return None


def should_rebalance(day: date) -> tuple[bool, str]:
    session = scheduled_session(day.year, day.month)
    if session is None:
        return False, "no open session in the first week of this month"
    if not is_trading_day(day):
        return False, f"market closed today; this month's session is {session.isoformat()}"
    if day != session:
        return False, f"not the rebalance session ({session.isoformat()})"
    return True, f"rebalance session {session.isoformat()}"


def month_already_rebalanced(runs_dir: Path, day: date) -> Path | None:
    prefix = day.strftime("%Y-%m")
    if not runs_dir.exists():
        return None
    for path in sorted(runs_dir.glob("*-entry.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("kind") != "monthly_rebalance":
            continue
        stamp = str(data.get("date") or "")
        if stamp.startswith(prefix):
            return path
    return None
