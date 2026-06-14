from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_tracker.domain.events import Event, EventType, SourceRef
from portfolio_tracker.domain.instruments import AssetClass, Instrument
from portfolio_tracker.reports.lots import FIFOPolicy, fifo


def _source() -> SourceRef:
    return SourceRef(file=Path("test.xlsx"), sheet="Cash operations", row=1)


def _instrument(symbol: str = "AAPL.US") -> Instrument:
    return Instrument(symbol=symbol, asset_class=AssetClass.EQUITY)


def _trade(
    event_id: str,
    quantity: str,
    price: str,
    ts: datetime,
    account_id: str = "PLN",
    symbol: str = "AAPL.US",
) -> Event:
    return Event(
        id=event_id,
        account_id=account_id,
        timestamp=ts,
        type=EventType.TRADE,
        amount=Decimal(quantity) * Decimal(price),
        currency="PLN",
        source=_source(),
        instrument=_instrument(symbol),
        quantity=Decimal(quantity),
        price=Decimal(price),
    )


T1 = datetime(2024, 1, 1, tzinfo=UTC)
T2 = datetime(2024, 2, 1, tzinfo=UTC)
T3 = datetime(2024, 3, 1, tzinfo=UTC)


def test_buy_only_leaves_open_lot() -> None:
    events = [_trade("e1", "10", "100", T1)]
    open_lots, closed = fifo(events)
    assert len(open_lots) == 1
    assert open_lots[0].remaining == Decimal("10")
    assert closed == []


def test_full_sell_closes_lot() -> None:
    events = [
        _trade("e1", "10", "100", T1),
        _trade("e2", "-10", "120", T2),
    ]
    open_lots, closed = fifo(events)
    assert open_lots == []
    assert len(closed) == 1
    assert closed[0].quantity == Decimal("10")
    assert closed[0].realized_pnl == Decimal("200")  # 10 * (120 - 100)


def test_partial_sell_leaves_remainder() -> None:
    events = [
        _trade("e1", "10", "100", T1),
        _trade("e2", "-3", "110", T2),
    ]
    open_lots, closed = fifo(events)
    assert open_lots[0].remaining == Decimal("7")
    assert closed[0].quantity == Decimal("3")
    assert closed[0].realized_pnl == Decimal("30")  # 3 * (110 - 100)


def test_fifo_order_two_lots() -> None:
    events = [
        _trade("e1", "5", "100", T1),
        _trade("e2", "5", "200", T2),
        _trade("e3", "-6", "300", T3),
    ]
    open_lots, closed = fifo(events)
    # First lot fully consumed, second partially consumed
    assert len(closed) == 2
    assert closed[0].buy_lot.event_id == "e1"
    assert closed[0].quantity == Decimal("5")
    assert closed[0].realized_pnl == Decimal("1000")  # 5 * (300 - 100)
    assert closed[1].buy_lot.event_id == "e2"
    assert closed[1].quantity == Decimal("1")
    assert closed[1].realized_pnl == Decimal("100")  # 1 * (300 - 200)
    assert open_lots[0].remaining == Decimal("4")


def test_non_trade_events_ignored() -> None:
    dividend = Event(
        id="d1",
        account_id="PLN",
        timestamp=T2,
        type=EventType.DIVIDEND,
        amount=Decimal("5"),
        currency="PLN",
        source=_source(),
        instrument=_instrument(),
    )
    events = [_trade("e1", "10", "100", T1), dividend]
    open_lots, closed = fifo(events)
    assert len(open_lots) == 1
    assert closed == []


def test_events_without_price_or_quantity_ignored() -> None:
    incomplete = Event(
        id="e2",
        account_id="PLN",
        timestamp=T2,
        type=EventType.TRADE,
        amount=Decimal("0"),
        currency="PLN",
        source=_source(),
        instrument=_instrument(),
        quantity=None,
        price=None,
    )
    events = [_trade("e1", "10", "100", T1), incomplete]
    open_lots, closed = fifo(events)
    assert open_lots[0].remaining == Decimal("10")


def test_fifo_policy_groups_by_account_and_symbol() -> None:
    events = [
        _trade("a1", "5", "100", T1, account_id="ACC1", symbol="AAPL.US"),
        _trade("a2", "-5", "150", T2, account_id="ACC1", symbol="AAPL.US"),
        _trade("b1", "3", "200", T1, account_id="ACC2", symbol="AAPL.US"),
        _trade("c1", "2", "50", T1, account_id="ACC1", symbol="MSFT.US"),
    ]
    policy = FIFOPolicy()
    closed = policy.match(events)
    # Only the ACC1/AAPL.US sell closes a lot
    assert len(closed) == 1
    assert closed[0].buy_lot.event_id == "a1"
