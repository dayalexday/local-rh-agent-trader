"""Rank S&P 500 names by the finished prior month's percent return.

Same helper the Cursor momentum agent uses (`scripts/rank_month.py` there).
"""
from __future__ import annotations

import io
import json
import logging
import warnings
from datetime import date

import pandas as pd
import requests
import yfinance as yf

from . import config


def load_sp500() -> list[str]:
    if config.SP500_CACHE.exists():
        return pd.read_csv(config.SP500_CACHE)["Symbol"].tolist()
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    symbols = [s.replace(".", "-") for s in pd.read_html(io.StringIO(html))[0]["Symbol"]]
    pd.DataFrame({"Symbol": symbols}).to_csv(config.SP500_CACHE, index=False)
    return symbols


def load_vehicles() -> dict[str, str]:
    if not config.VEHICLES_PATH.exists():
        return {}
    raw = json.loads(config.VEHICLES_PATH.read_text())
    return {key: value for key, value in raw.items() if not str(key).startswith("_")}


def month_window(as_of: pd.Timestamp, complete_prior: bool) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    if complete_prior:
        period = as_of.to_period("M") - 1
        start = period.start_time - pd.Timedelta(days=10)
        end = period.end_time + pd.Timedelta(days=1)
        return start, end, str(period)
    period = as_of.to_period("M")
    start = (period - 1).end_time - pd.Timedelta(days=5)
    end = as_of + pd.Timedelta(days=1)
    return start, end, f"{period}-MTD"


def rank_month(
    as_of: date | None = None,
    *,
    complete_prior: bool = True,
    top: int = 20,
) -> dict:
    as_of_ts = pd.Timestamp(as_of) if as_of else pd.Timestamp.today().normalize()
    start, end, label = month_window(as_of_ts, complete_prior)
    vehicles = load_vehicles()
    symbols = load_sp500()
    print(
        f"Ranking {len(symbols)} names for {label} "
        f"(download {start.date()} → {end.date()})...",
        flush=True,
    )
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        prices = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
            threads=True,
        )["Close"]
    prices = prices.dropna(how="all")

    if complete_prior:
        period = as_of_ts.to_period("M") - 1
        month_px = prices.loc[str(period)]
        first, last = month_px.iloc[0], month_px.iloc[-1]
        window = f"{month_px.index[0].date()} → {month_px.index[-1].date()}"
    else:
        prior_end = prices.loc[: (as_of_ts.to_period("M") - 1).end_time].index[-1]
        latest = prices.loc[:as_of_ts].index[-1]
        first, last = prices.loc[prior_end], prices.loc[latest]
        window = f"{prior_end.date()} → {latest.date()}"

    returns = (last / first - 1).dropna().sort_values(ascending=False)
    rows = []
    for index, (symbol, change) in enumerate(returns.head(top).items(), 1):
        vehicle = vehicles.get(str(symbol)) or ""
        rows.append({
            "rank": index,
            "underlying": str(symbol),
            "return_pct": round(float(change) * 100, 2),
            "preferred_vehicle": vehicle,
        })
        print(f"{index:2} {symbol:8} {float(change) * 100:7.2f}%  →  {vehicle or symbol}", flush=True)

    payload = {
        "label": label,
        "as_of": str(as_of_ts.date()),
        "window": window,
        "complete_prior": complete_prior,
        "top": rows,
    }
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = config.RUNS_DIR / f"rank-{label}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {out}", flush=True)
    return payload
