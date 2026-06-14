from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from portfolio_tracker.domain.events import Event, EventType, SourceRef
from portfolio_tracker.domain.instruments import AssetClass, Instrument


def test_event_round_trip(sample_event: Event) -> None:
    restored = Event.from_dict(sample_event.to_dict())
    assert restored.id == sample_event.id
    assert restored.amount == sample_event.amount
    assert restored.quantity == sample_event.quantity
    assert restored.fees == sample_event.fees
    assert restored.timestamp == sample_event.timestamp


def test_decimal_preserved_through_serialization(sample_event: Event) -> None:
    restored = Event.from_dict(sample_event.to_dict())
    assert isinstance(restored.amount, Decimal)
    assert isinstance(restored.quantity, Decimal)
    assert isinstance(restored.fees, Decimal)


def test_event_is_frozen(sample_event: Event) -> None:
    with pytest.raises((AttributeError, TypeError)):
        sample_event.id = "other"  # type: ignore[misc]


def test_event_with_instrument(sample_source: SourceRef) -> None:
    instr = Instrument(symbol="AAPL.US", asset_class=AssetClass.EQUITY, quote_currency="USD")
    event = Event(
        id="test:1",
        account_id="XTB_USD",
        timestamp=datetime(2024, 6, 1, tzinfo=UTC),
        type=EventType.TRADE,
        amount=Decimal("-5000"),
        currency="USD",
        source=sample_source,
        instrument=instr,
        quantity=Decimal("10"),
        price=Decimal("500"),
    )
    restored = Event.from_dict(event.to_dict())
    assert restored.instrument is not None
    assert restored.instrument.symbol == "AAPL.US"
    assert restored.instrument.quote_currency == "USD"


def test_event_none_instrument_round_trips(sample_event: Event) -> None:
    assert sample_event.instrument is None
    restored = Event.from_dict(sample_event.to_dict())
    assert restored.instrument is None


def test_source_ref_excluded_from_hash(tmp_file: Path) -> None:  # noqa: F821
    from pathlib import Path

    s1 = SourceRef(file=Path("/a.xlsx"), sheet="Cash operations", row=1, raw={"x": 1})
    s2 = SourceRef(file=Path("/a.xlsx"), sheet="Cash operations", row=1, raw={"y": 999})
    assert hash(s1) == hash(s2)
