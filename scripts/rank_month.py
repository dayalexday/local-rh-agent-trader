#!/usr/bin/env python3
"""Offline rank. Same numbers the monthly job hands to the model."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_rh_agent.rank import rank_month  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--complete-prior",
        action="store_true",
        help="Rank the finished prior calendar month (this is the default).",
    )
    parser.add_argument(
        "--mtd",
        action="store_true",
        help="Rank the current month-to-date instead of the finished prior month.",
    )
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    rank_month(as_of, complete_prior=not args.mtd, top=args.top)


if __name__ == "__main__":
    main()
