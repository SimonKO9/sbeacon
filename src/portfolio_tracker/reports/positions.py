from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from portfolio_tracker.domain.events import Event, EventType
from portfolio_tracker.reports.lots import fifo


@dataclass
class Position:
    symbol: str
    account_id: str
    quantity: Decimal
    avg_cost: Decimal  # per-share cost in account base currency
    currency: str
    # TODO: market_value — needs current prices from pricing provider
    # TODO: unrealized_pnl — needs current prices
    # TODO: weight_pct — needs total portfolio market value


def compute_positions(events: list[Event]) -> list[Position]:
    """Derive open positions from TRADE events using FIFO lot matching.

    Groups events by (account_id, symbol) and runs FIFO per group.
    TODO: use wrapper-pool scope for REGULAR accounts (§7) — currently per-account.
    """
    groups: defaultdict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in events:
        if event.type == EventType.TRADE and event.instrument:
            groups[(event.account_id, event.instrument.symbol)].append(event)

    positions: list[Position] = []
    for (account_id, symbol), group_events in sorted(groups.items()):
        open_lots, _ = fifo(group_events)
        if not open_lots:
            continue

        total_qty = sum((lot.remaining for lot in open_lots), Decimal(0))
        if total_qty == 0:
            continue

        total_cost = sum((lot.remaining * lot.price for lot in open_lots), Decimal(0))
        currency = open_lots[0].currency

        positions.append(
            Position(
                symbol=symbol,
                account_id=account_id,
                quantity=total_qty,
                avg_cost=total_cost / total_qty,
                currency=currency,
            )
        )

    return positions
