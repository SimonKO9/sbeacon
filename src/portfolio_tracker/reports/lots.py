from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from portfolio_tracker.domain.events import Event, EventType


@dataclass
class Lot:
    event_id: str
    symbol: str
    account_id: str
    quantity: Decimal
    price: Decimal        # exchange price (display only)
    currency: str         # account base currency
    remaining: Decimal
    cost_per_unit: Decimal  # per-unit cost in account base currency (|amount| / |qty|)


@dataclass
class ClosedLot:
    buy_lot: Lot
    sell_event_id: str
    quantity: Decimal
    realized_pnl: Decimal  # in account base currency (buy_lot.currency)


class LotMatchingPolicy(Protocol):
    def match(self, events: list[Event]) -> list[ClosedLot]: ...


def fifo(events: list[Event]) -> tuple[list[Lot], list[ClosedLot]]:
    """FIFO lot matching for TRADE events belonging to one (account, symbol).

    Returns (open_lots, closed_lots). Expects signed quantity on events
    (positive = buy, negative = sell).
    """
    open_queue: deque[Lot] = deque()
    closed: list[ClosedLot] = []

    for event in sorted(events, key=lambda e: e.timestamp):
        if event.type != EventType.TRADE:
            continue
        if event.quantity is None or event.price is None or event.instrument is None:
            continue

        qty = event.quantity

        if qty > 0:
            cost_per_unit = abs(event.amount) / qty
            open_queue.append(
                Lot(
                    event_id=event.id,
                    symbol=event.instrument.symbol,
                    account_id=event.account_id,
                    quantity=qty,
                    price=event.price,
                    currency=event.currency,
                    remaining=qty,
                    cost_per_unit=cost_per_unit,
                )
            )
        elif qty < 0:
            sell_qty = -qty
            sell_cost_per_unit = abs(event.amount) / sell_qty
            while sell_qty > 0 and open_queue:
                lot = open_queue[0]
                matched = min(lot.remaining, sell_qty)
                closed.append(
                    ClosedLot(
                        buy_lot=lot,
                        sell_event_id=event.id,
                        quantity=matched,
                        realized_pnl=matched * (sell_cost_per_unit - lot.cost_per_unit),
                    )
                )
                lot.remaining -= matched
                sell_qty -= matched
                if lot.remaining == 0:
                    open_queue.popleft()

    return list(open_queue), closed


class FIFOPolicy:
    def match(self, events: list[Event]) -> list[ClosedLot]:
        # TODO: use wrapper-pool scope for REGULAR accounts (§7) — currently per-account
        groups: defaultdict[tuple[str, str], list[Event]] = defaultdict(list)
        for event in events:
            if event.type == EventType.TRADE and event.instrument:
                groups[(event.account_id, event.instrument.symbol)].append(event)

        closed: list[ClosedLot] = []
        for group_events in groups.values():
            _, group_closed = fifo(group_events)
            closed.extend(group_closed)
        return closed
