from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import duckdb
import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.nbp.pl/api/exchangerates/rates/a"

_DDL = """
CREATE TABLE IF NOT EXISTS nbp_fx_cache (
    currency   VARCHAR NOT NULL,
    trade_date DATE    NOT NULL,
    rate       VARCHAR NOT NULL,
    PRIMARY KEY (currency, trade_date)
)
"""


class FxProvider(Protocol):
    def rate(self, from_ccy: str, to_ccy: str, on: date) -> Decimal: ...


class NBPProvider:
    """NBP official exchange rates (Polish National Bank).

    `rate(ccy, 'PLN', on=transaction_date)` returns the D-1 rate: the rate
    published on the last business day *before* `on`, as required by PIT-38.
    """

    def __init__(self, timeout: int = 10) -> None:
        self._timeout = timeout

    def rate(self, from_ccy: str, to_ccy: str, on: date) -> Decimal:
        if to_ccy.upper() != "PLN":
            raise ValueError(f"NBP only provides rates to PLN, got {to_ccy!r}")
        if from_ccy.upper() == "PLN":
            return Decimal("1")
        # D-1: start from the day before the transaction, walk back to find a
        # business day with a published NBP Table A rate (max 7 days back).
        d1 = on - timedelta(days=1)
        return self._find_rate(from_ccy.lower(), d1)

    def _find_rate(self, ccy_lower: str, starting: date) -> Decimal:
        for delta in range(7):
            d = starting - timedelta(days=delta)
            resp = requests.get(
                f"{_BASE_URL}/{ccy_lower}/{d.isoformat()}/",
                params={"format": "json"},
                timeout=self._timeout,
            )
            if resp.status_code == 200:
                return Decimal(str(resp.json()["rates"][0]["mid"]))
            if resp.status_code != 404:
                resp.raise_for_status()
        raise LookupError(
            f"No NBP Table A rate for {ccy_lower.upper()} within 7 days before {starting}"
        )


class CachedNBPProvider:
    """Persistent DuckDB-backed cache around NBPProvider.

    Caches by (currency, trade_date) so each unique trade date is fetched
    at most once.  Results are stored in the prices.duckdb file alongside
    Yahoo Finance price cache.
    """

    def __init__(self, db_path: Path, inner: NBPProvider | None = None) -> None:
        self._db_path = db_path
        self._inner = inner or NBPProvider()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(db_path))
        con.execute(_DDL)
        con.close()

    def rate(self, from_ccy: str, to_ccy: str, on: date) -> Decimal:
        if to_ccy.upper() != "PLN":
            raise ValueError(f"NBP only provides rates to PLN, got {to_ccy!r}")
        if from_ccy.upper() == "PLN":
            return Decimal("1")

        ccy = from_ccy.upper()
        con = duckdb.connect(str(self._db_path), read_only=True)
        row = con.execute(
            "SELECT rate FROM nbp_fx_cache WHERE currency = ? AND trade_date = ?",
            [ccy, on],
        ).fetchone()
        con.close()

        if row is not None:
            return Decimal(row[0])

        fetched = self._inner.rate(from_ccy, to_ccy, on)
        logger.info("NBP D-1 %s/%s: %s", ccy, on, fetched)

        con = duckdb.connect(str(self._db_path))
        con.execute(
            "INSERT INTO nbp_fx_cache (currency, trade_date, rate) VALUES (?, ?, ?)"
            " ON CONFLICT (currency, trade_date) DO UPDATE SET rate = EXCLUDED.rate",
            [ccy, on, str(fetched)],
        )
        con.commit()
        con.close()

        return fetched
