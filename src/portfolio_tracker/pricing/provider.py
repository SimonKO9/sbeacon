from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    currency: str
    as_of: date
    source: str


@dataclass(frozen=True)
class Bar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None


class PriceProvider(Protocol):
    def latest(self, symbols: list[str]) -> dict[str, Quote]: ...
    def history(self, symbol: str, start: date, end: date) -> list[Bar]: ...
