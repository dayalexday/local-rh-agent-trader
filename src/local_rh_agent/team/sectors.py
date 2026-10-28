"""Coarse sector tags. Names missing from sectors.json are Unknown, which is itself capped."""
from __future__ import annotations

import json

from .. import config

_CACHE: dict[str, str] | None = None


def load_sectors() -> dict[str, str]:
    global _CACHE
    if _CACHE is None:
        if config.SECTORS_PATH.exists():
            raw = json.loads(config.SECTORS_PATH.read_text())
            _CACHE = {
                str(key).upper(): str(value)
                for key, value in raw.items()
                if not str(key).startswith("_")
            }
        else:
            _CACHE = {}
    return _CACHE


def sector_for(symbol: str, table: dict[str, str] | None = None) -> str:
    lookup = load_sectors() if table is None else table
    return lookup.get(symbol.upper(), "Unknown")
