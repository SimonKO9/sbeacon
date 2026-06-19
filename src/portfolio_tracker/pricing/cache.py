from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from .provider import Bar, PriceProvider, Quote

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS price_cache (
    provider  VARCHAR NOT NULL,
    symbol    VARCHAR NOT NULL,
    date      DATE    NOT NULL,
    price     VARCHAR NOT NULL,
    currency  VARCHAR NOT NULL,
    source    VARCHAR NOT NULL,
    PRIMARY KEY (provider, symbol, date)
)
"""


def _open(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(_DDL)
    return con


class CachingProvider:
    """Persistent DuckDB-backed cache wrapping any PriceProvider."""

    def __init__(
        self,
        inner: PriceProvider,
        db_path: Path,
        provider_name: str,
        max_staleness_days: int = 1,
    ) -> None:
        self._inner = inner
        self._db_path = db_path
        self._name = provider_name
        self._max_staleness = max_staleness_days
        con = _open(db_path)
        con.close()

    def latest(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        cached = self._read_cache(symbols)
        misses = [s for s in symbols if s not in cached]
        if misses:
            fetched = self._inner.latest(misses)
            if fetched:
                self._write_cache(fetched)
            cached.update(fetched)
        return cached

    def history(self, symbol: str, start: date, end: date) -> list[Bar]:
        return self._inner.history(symbol, start, end)

    def _read_cache(self, symbols: list[str]) -> dict[str, Quote]:
        cutoff = date.today() - timedelta(days=self._max_staleness)
        placeholders = ",".join("?" * len(symbols))
        con = duckdb.connect(str(self._db_path), read_only=True)
        try:
            rows = con.execute(
                f"SELECT symbol, price, currency, date FROM price_cache"
                f" WHERE provider = ? AND symbol IN ({placeholders}) AND date >= ?",
                [self._name, *symbols, cutoff],
            ).fetchall()
        finally:
            con.close()
        return {
            r[0]: Quote(symbol=r[0], price=Decimal(r[1]), currency=r[2], as_of=r[3], source=self._name)
            for r in rows
        }

    def _write_cache(self, quotes: dict[str, Quote]) -> None:
        con = _open(self._db_path)
        try:
            for q in quotes.values():
                con.execute(
                    "INSERT INTO price_cache (provider, symbol, date, price, currency, source)"
                    " VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (provider, symbol, date) DO UPDATE SET"
                    "   price = EXCLUDED.price, currency = EXCLUDED.currency",
                    [self._name, q.symbol, q.as_of, str(q.price), q.currency, q.source],
                )
            con.commit()
        finally:
            con.close()
