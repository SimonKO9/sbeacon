from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from portfolio_tracker.domain.events import Event, EventType, SourceRef
from portfolio_tracker.domain.instruments import AssetClass, Instrument
from portfolio_tracker.reports.positions import compute_positions


def _source() -> SourceRef:
    return SourceRef(file=Path("test.xlsx"), sheet="Cash operations", row=1)


def _trade(
    event_id: str,
    symbol: str,
    quantity: str,
    price: str,
    ts: datetime,
    account_id: str = "PLN",
    currency: str = "PLN",
) -> Event:
    return Event(
        id=event_id,
        account_id=account_id,
        timestamp=ts,
        type=EventType.TRADE,
        amount=Decimal(quantity) * Decimal(price),
        currency=currency,
        source=_source(),
        instrument=Instrument(symbol=symbol, asset_class=AssetClass.EQUITY),
        quantity=Decimal(quantity),
        price=Decimal(price),
    )


T1 = datetime(2024, 1, 1, tzinfo=UTC)
T2 = datetime(2024, 2, 1, tzinfo=UTC)
T3 = datetime(2024, 3, 1, tzinfo=UTC)


def test_single_buy_creates_position() -> None:
    events = [_trade("e1", "AAPL.US", "10", "100", T1)]
    posns = compute_positions(events)
    assert len(posns) == 1
    assert posns[0].symbol == "AAPL.US"
    assert posns[0].quantity == Decimal("10")
    assert posns[0].avg_cost == Decimal("100")
    assert posns[0].currency == "PLN"


def test_fully_sold_position_excluded() -> None:
    events = [
        _trade("e1", "AAPL.US", "10", "100", T1),
        _trade("e2", "AAPL.US", "-10", "120", T2),
    ]
    assert compute_positions(events) == []


def test_avg_cost_weighted_by_quantity() -> None:
    events = [
        _trade("e1", "AAPL.US", "4", "100", T1),
        _trade("e2", "AAPL.US", "6", "200", T2),
    ]
    posns = compute_positions(events)
    assert posns[0].quantity == Decimal("10")
    # (4*100 + 6*200) / 10 = 1600/10 = 160
    assert posns[0].avg_cost == Decimal("160")


def test_partial_sell_updates_avg_cost() -> None:
    events = [
        _trade("e1", "AAPL.US", "4", "100", T1),
        _trade("e2", "AAPL.US", "6", "200", T2),
        _trade("e3", "AAPL.US", "-4", "250", T3),  # sells the first lot (FIFO)
    ]
    posns = compute_positions(events)
    assert posns[0].quantity == Decimal("6")
    assert posns[0].avg_cost == Decimal("200")  # only the second lot remains


def test_multiple_symbols_and_accounts() -> None:
    events = [
        _trade("a1", "AAPL.US", "5", "100", T1, account_id="PLN"),
        _trade("b1", "MSFT.US", "3", "300", T1, account_id="PLN"),
        _trade("c1", "AAPL.US", "2", "150", T1, account_id="EUR", currency="EUR"),
    ]
    posns = compute_positions(events)
    assert len(posns) == 3
    symbols_accounts = {(p.symbol, p.account_id) for p in posns}
    assert ("AAPL.US", "PLN") in symbols_accounts
    assert ("MSFT.US", "PLN") in symbols_accounts
    assert ("AAPL.US", "EUR") in symbols_accounts


def test_non_trade_events_ignored() -> None:
    events = [
        _trade("e1", "AAPL.US", "10", "100", T1),
        Event(
            id="d1",
            account_id="PLN",
            timestamp=T2,
            type=EventType.DIVIDEND,
            amount=Decimal("50"),
            currency="PLN",
            source=_source(),
            instrument=Instrument(symbol="AAPL.US", asset_class=AssetClass.EQUITY),
        ),
    ]
    posns = compute_positions(events)
    assert len(posns) == 1
    assert posns[0].quantity == Decimal("10")
