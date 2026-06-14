from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol


class FxProvider(Protocol):
    def rate(self, from_ccy: str, to_ccy: str, on: date) -> Decimal: ...


class NBPProvider:
    """NBP official exchange rates (Polish National Bank, D-1 convention)."""

    def rate(self, from_ccy: str, to_ccy: str, on: date) -> Decimal:
        # TODO: call https://api.nbp.pl/api/exchangerates/rates/a/{from_ccy}/{on}/
        raise NotImplementedError
