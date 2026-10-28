"""Price panels for the offline team run and the backtest. No network."""
from __future__ import annotations

import csv
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


@dataclass
class PricePanel:
    dates: list[date]
    prices: dict[str, list[float]]
    sectors: dict[str, str]
    vehicles: dict[str, str] = field(default_factory=dict)

    @property
    def universe(self) -> list[str]:
        return list(self.sectors)


def month_ends(start: date, count: int) -> list[date]:
    dates: list[date] = []
    year, month = start.year, start.month
    for _ in range(count):
        if month == 12:
            nxt = date(year + 1, 1, 1)
        else:
            nxt = date(year, month + 1, 1)
        dates.append(nxt - timedelta(days=1))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return dates


# A small universe with real tickers and the single-stock vehicles from vehicles.json.
# Prices are synthetic. The names are only there so memos are readable.
_UNIVERSE: tuple[tuple[str, str, str], ...] = (
    ("NVDA", "Technology", "NVDU"),
    ("AAPL", "Technology", "AAPU"),
    ("MSFT", "Technology", "MSFU"),
    ("AMD", "Technology", "AMUU"),
    ("MU", "Technology", "MUU"),
    ("AVGO", "Technology", "AVL"),
    ("JPM", "Financials", ""),
    ("GS", "Financials", ""),
    ("V", "Financials", ""),
    ("LLY", "Health Care", "ELIL"),
    ("UNH", "Health Care", "UNHU"),
    ("AMZN", "Consumer Discretionary", "AMZU"),
    ("TSLA", "Consumer Discretionary", "TSLL"),
    ("XOM", "Energy", "XOMX"),
    ("CVX", "Energy", ""),
    ("CAT", "Industrials", ""),
    ("BA", "Industrials", "BOEU"),
    ("META", "Communication Services", "METU"),
)


def synthetic_panel(seed: int = 7, months: int = 18) -> PricePanel:
    """Seeded monthly closes. Leveraged vehicles are 2× the underlying's monthly move.

    That is not how a daily-reset 2× ETF works. The backtest report says so.
    """
    rng = random.Random(seed)
    dates = month_ends(date(2024, 1, 31), months)
    sectors = {symbol: sector for symbol, sector, _vehicle in _UNIVERSE}
    vehicles = {symbol: vehicle for symbol, _sector, vehicle in _UNIVERSE if vehicle}
    prices: dict[str, list[float]] = {symbol: [100.0] for symbol, _sector, _vehicle in _UNIVERSE}
    for vehicle in vehicles.values():
        prices[vehicle] = [100.0]
    prices["SPY"] = [100.0]
    previous = {symbol: 0.0 for symbol in sectors}
    sector_names = sorted(set(sectors.values()))

    for month in range(1, months):
        sector_shock = {name: rng.gauss(0, 0.03) for name in sector_names}
        tech_drift = 0.035 if month < months * 0.45 else 0.0
        if month == 11:
            tech_drift -= 0.18
        spy = max(min(0.008 + rng.gauss(0, 0.025), 0.12), -0.12)
        prices["SPY"].append(round(prices["SPY"][-1] * (1 + spy), 4))
        for symbol, sector, vehicle in _UNIVERSE:
            drift = tech_drift if sector == "Technology" else 0.004
            ret = drift + sector_shock[sector] + 0.25 * previous[symbol] + rng.gauss(0, 0.05)
            ret = max(min(ret, 0.35), -0.40)
            previous[symbol] = ret
            prices[symbol].append(round(prices[symbol][-1] * (1 + ret), 4))
            if vehicle:
                prices[vehicle].append(round(prices[vehicle][-1] * (1 + 2 * ret), 4))
    return PricePanel(dates=dates, prices=prices, sectors=sectors, vehicles=vehicles)


def load_price_csv(path: Path) -> PricePanel:
    """CSV columns: date, symbol, close, sector, vehicle.

    `vehicle` is set on the underlying row and names the leveraged ticker, which
    has its own rows. SPY has an empty sector and an empty vehicle.
    """
    buckets: dict[str, list[tuple[date, float]]] = {}
    sectors: dict[str, str] = {}
    vehicles: dict[str, str] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = row["symbol"].strip().upper()
            buckets.setdefault(symbol, []).append((date.fromisoformat(row["date"].strip()), float(row["close"])))
            sector = (row.get("sector") or "").strip()
            vehicle = (row.get("vehicle") or "").strip().upper()
            if sector:
                sectors[symbol] = sector
            if vehicle:
                vehicles[symbol] = vehicle
    if not buckets:
        raise ValueError(f"No prices in {path}")
    dates = sorted({stamp for series in buckets.values() for stamp, _price in series})
    prices: dict[str, list[float]] = {}
    for symbol, series in buckets.items():
        lookup = {stamp: price for stamp, price in series}
        if any(stamp not in lookup for stamp in dates):
            raise ValueError(f"{symbol} is missing a month in {path}")
        prices[symbol] = [lookup[stamp] for stamp in dates]
    sectors = {symbol: sector for symbol, sector in sectors.items() if symbol in prices and symbol != "SPY"}
    return PricePanel(dates=dates, prices=prices, sectors=sectors, vehicles=vehicles)


def offline_as_of_index(panel: PricePanel) -> int:
    """Latest March or September, so the offline demo includes the planted sentiment veto."""
    for index in range(len(panel.dates) - 1, 0, -1):
        if panel.dates[index].month in (3, 9):
            return index
    return len(panel.dates) - 1


def rank_at(panel: PricePanel, index: int, top: int) -> dict:
    """Rank the finished month ending at `panel.dates[index]`."""
    if index < 1:
        raise ValueError("need a prior month to rank")
    returns: dict[str, float] = {}
    for symbol in panel.universe:
        first = panel.prices[symbol][index - 1]
        last = panel.prices[symbol][index]
        if first:
            returns[symbol] = (last / first - 1.0) * 100.0
    ordered = sorted(returns.items(), key=lambda item: (-item[1], item[0]))[:top]
    rows = []
    for rank, (symbol, change) in enumerate(ordered, 1):
        rows.append({
            "rank": rank,
            "underlying": symbol,
            "return_pct": round(change, 2),
            "preferred_vehicle": panel.vehicles.get(symbol, ""),
            "sector": panel.sectors.get(symbol, "Unknown"),
        })
    start = panel.dates[index - 1]
    end = panel.dates[index]
    return {
        "label": end.strftime("%Y-%m"),
        "as_of": end.isoformat(),
        "window": f"{start.isoformat()} → {end.isoformat()}",
        "complete_prior": True,
        "top": rows,
    }


def panel_return_pct(panel: PricePanel, symbol: str, start: date, end: date) -> float | None:
    if symbol not in panel.prices:
        return None
    try:
        i0 = panel.dates.index(start)
        i1 = panel.dates.index(end)
    except ValueError:
        return None
    first = panel.prices[symbol][i0]
    last = panel.prices[symbol][i1]
    if first <= 0:
        return None
    return (last / first - 1.0) * 100.0


def headlines_for_ranking(ranking: dict, as_of: date) -> Callable[[str], list[str]]:
    """Offline headlines. March and September plant a veto on the top momentum name.

    The headlines are synthetic so the veto path shows up in CI. They are not news.
    """
    planted: set[str] = set()
    if as_of.month in (3, 9) and ranking.get("top"):
        planted.add(str(ranking["top"][0]["underlying"]))

    def headlines(symbol: str) -> list[str]:
        if symbol in planted:
            return [
                f"{symbol} faces fraud investigation and shareholder lawsuit",
                f"Analysts downgrade {symbol} after federal probe",
            ]
        return []

    return headlines
