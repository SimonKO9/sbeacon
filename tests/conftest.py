from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_tracker.domain.accounts import Account, Wrapper
from portfolio_tracker.domain.events import Event, EventType, SourceRef


@pytest.fixture
def tmp_file(tmp_path: Path) -> Path:
    return tmp_path / "test.xlsx"


@pytest.fixture
def sample_source(tmp_file: Path) -> SourceRef:
    return SourceRef(file=tmp_file, sheet="Cash operations", row=5)


@pytest.fixture
def sample_event(sample_source: SourceRef) -> Event:
    return Event(
        id="XTB_PLN:12345",
        account_id="XTB_PLN",
        timestamp=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
        type=EventType.TRADE,
        amount=Decimal("-10250.00"),
        currency="PLN",
        source=sample_source,
        quantity=Decimal("100"),
        price=Decimal("102.50"),
    )


@pytest.fixture
def pln_account() -> Account:
    return Account(
        account_id="XTB_PLN",
        broker="XTB",
        wrapper=Wrapper.REGULAR,
        base_currency="PLN",
    )
