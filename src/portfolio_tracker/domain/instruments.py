from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AssetClass(StrEnum):
    EQUITY = "equity"
    FIXED_INCOME = "fixed-income"
    COMMODITY = "commodity"
    CRYPTO = "crypto"
    CASH = "cash"
    REAL_ESTATE = "real-estate"


class Role(StrEnum):
    CORE = "core"
    SATELLITE = "satellite"
    THEMATIC = "thematic"
    REAL_ASSETS = "real-assets"
    CRYPTO = "crypto"
    FIXED_INCOME = "fixed-income"
    CASH = "cash"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str | None = None
    asset_class: AssetClass = AssetClass.EQUITY
    quote_currency: str | None = None
    isin: str | None = None
    exchange: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_class": self.asset_class.value,
            "quote_currency": self.quote_currency,
            "isin": self.isin,
            "exchange": self.exchange,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Instrument:
        return cls(
            symbol=d["symbol"],
            name=d.get("name"),
            asset_class=AssetClass(d.get("asset_class", "equity")),
            quote_currency=d.get("quote_currency"),
            isin=d.get("isin"),
            exchange=d.get("exchange"),
        )
