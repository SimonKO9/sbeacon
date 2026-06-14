from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_tracker.adapters.xtb.normalize import normalize
from portfolio_tracker.domain.accounts import Account, Wrapper
from portfolio_tracker.domain.events import EventType


@pytest.fixture
def account() -> Account:
    return Account(
        account_id="XTB_PLN",
        broker="XTB",
        wrapper=Wrapper.REGULAR,
        base_currency="PLN",
    )


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    return tmp_path / "PLN_export.xlsx"


def _raw(type_str: str, **kwargs: object) -> dict:
    return {
        "id": "XTB_PLN:99",
        "type_str": type_str,
        "timestamp": datetime(2024, 3, 1, 12, 0, tzinfo=UTC),
        "amount": Decimal("10.00"),
        "symbol": None,
        "comment_parsed": {},
        "raw": {},
        "source_row": 5,
        **kwargs,
    }


@pytest.mark.parametrize(
    "type_str, expected",
    [
        ("Stock purchase", EventType.TRADE),
        ("Stock sell", EventType.TRADE),
        ("Dividend", EventType.DIVIDEND),
        ("Dividend from foreign company on PL market", EventType.DIVIDEND),
        ("Free funds interest", EventType.INTEREST),
        ("Free funds interest tax", EventType.TAX),
        ("Withholding tax", EventType.TAX),
        ("SEC fee", EventType.FEE),
        ("Deposit", EventType.DEPOSIT),
        ("Transfer", EventType.FX_CONVERSION),
        ("Subaccount transfer", EventType.TRANSFER),
        ("IKE deposit", EventType.TRANSFER),
        ("IKZE deposit", EventType.TRANSFER),
        # case-insensitive
        ("withholding tax", EventType.TAX),
        ("STOCK PURCHASE", EventType.TRADE),
    ],
)
def test_type_map(
    type_str: str, expected: EventType, account: Account, source_file: Path
) -> None:
    event = normalize(_raw(type_str), account, source_file)
    assert event is not None
    assert event.type == expected


def test_unrecognized_type_returns_none(account: Account, source_file: Path) -> None:
    event = normalize(_raw("Unknown future type"), account, source_file)
    assert event is None


def test_missing_amount_returns_none(account: Account, source_file: Path) -> None:
    event = normalize(_raw("Deposit", amount=None), account, source_file)
    assert event is None


def test_bad_timestamp_returns_none(account: Account, source_file: Path) -> None:
    event = normalize(_raw("Deposit", timestamp="not-a-date"), account, source_file)
    assert event is None


def test_withholding_tax_carries_symbol(account: Account, source_file: Path) -> None:
    raw = _raw("Withholding tax", symbol="AAPL.US", amount=Decimal("-1.50"))
    event = normalize(raw, account, source_file)
    assert event is not None
    assert event.type == EventType.TAX
    assert event.instrument is not None
    assert event.instrument.symbol == "AAPL.US"
    assert event.amount == Decimal("-1.50")


def test_event_ids_and_account(account: Account, source_file: Path) -> None:
    event = normalize(_raw("Deposit"), account, source_file)
    assert event is not None
    assert event.id == "XTB_PLN:99"
    assert event.account_id == "XTB_PLN"
    assert event.currency == "PLN"
