from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from portfolio_tracker.domain.events import Event


@dataclass
class Lot:
    event_id: str
    symbol: str
    quantity: Decimal
    price: Decimal
    currency: str
    remaining: Decimal


@dataclass
class ClosedLot:
    buy_lot: Lot
    sell_event_id: str
    quantity: Decimal
    realized_pnl: Decimal


class LotMatchingPolicy(Protocol):
    def match(self, events: list[Event]) -> list[ClosedLot]: ...


class FIFOPolicy:
    def match(self, events: list[Event]) -> list[ClosedLot]:
        # TODO: implement FIFO lot matching
        return []
