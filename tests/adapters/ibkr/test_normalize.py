from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from portfolio_tracker.adapters.ibkr.normalize import normalize
from portfolio_tracker.domain.accounts import Account, Wrapper
from portfolio_tracker.domain.events import EventType


@pytest.fixture
def account() -> Account:
    return Account(
        account_id="IBKR_U12345678",
        broker="IBKR",
        wrapper=Wrapper.REGULAR,
        base_currency="PLN",
    )


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    return tmp_path / "U12345678.TRANSACTIONS.1Y.csv"


@pytest.fixture(autouse=True)
def no_network_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent symbol resolution from making real HTTP calls in unit tests."""
    import portfolio_tracker.adapters.ibkr.normalize as norm_mod
    norm_mod._resolution_cache.clear()
    monkeypatch.setattr(norm_mod, "_resolve_symbol", lambda symbol, currency: symbol)


def _raw(txn_type: str, **kwargs: object) -> dict:
    return {
        "id": "IBKR_U12345678:abc123",
        "txn_type": txn_type,
        "date_str": "2026-05-28",
        "description": "Test",
        "symbol": None,
        "quantity": None,
        "price": None,
        "price_currency": None,
        "gross_amount": None,
        "commission": None,
        "net_amount": Decimal("100.00"),
        "exchange_rate": Decimal("1.0"),
        "raw": {},
        "source_row": 5,
        **kwargs,
    }


@pytest.mark.parametrize(
    "txn_type, expected",
    [
        ("Buy", EventType.TRADE),
        ("Sell", EventType.TRADE),
        ("Deposit", EventType.DEPOSIT),
        ("Withdrawal", EventType.WITHDRAWAL),
        ("Forex Trade Component", EventType.FX_CONVERSION),
        ("buy", EventType.TRADE),
        ("DEPOSIT", EventType.DEPOSIT),
    ],
)
def test_known_types_map(
    txn_type: str, expected: EventType, account: Account, source_file: Path
) -> None:
    event = normalize(_raw(txn_type), account, source_file)
    assert event is not None
    assert event.type == expected


def test_adjustment_skipped(account: Account, source_file: Path) -> None:
    event = normalize(_raw("Adjustment"), account, source_file)
    assert event is None


def test_unknown_type_skipped(account: Account, source_file: Path) -> None:
    event = normalize(_raw("SomeNewType"), account, source_file)
    assert event is None


def test_buy_event(account: Account, source_file: Path) -> None:
    raw = _raw(
        "Buy",
        symbol="PRX",
        description="PROSUS NV",
        quantity=Decimal("108.0"),
        price=Decimal("38.6"),
        price_currency="EUR",
        commission=Decimal("-12.6825"),
        net_amount=Decimal("-17636.2845"),
    )
    event = normalize(raw, account, source_file)
    assert event is not None
    assert event.type == EventType.TRADE
    assert event.quantity == Decimal("108.0")
    assert event.price == Decimal("38.6")
    assert event.amount == Decimal("-17636.2845")
    assert event.fees == Decimal("12.6825")
    assert event.instrument is not None
    assert event.instrument.symbol == "PRX"
    assert event.instrument.quote_currency == "EUR"
    assert event.currency == "PLN"


def test_sell_quantity_is_negative(account: Account, source_file: Path) -> None:
    raw = _raw(
        "Sell",
        symbol="PRX",
        quantity=Decimal("50.0"),
        price=Decimal("40.0"),
        price_currency="EUR",
        net_amount=Decimal("2000.0"),
    )
    event = normalize(raw, account, source_file)
    assert event is not None
    assert event.quantity == Decimal("-50.0")


def test_deposit_no_instrument(account: Account, source_file: Path) -> None:
    raw = _raw("Deposit", net_amount=Decimal("35000.0"))
    event = normalize(raw, account, source_file)
    assert event is not None
    assert event.type == EventType.DEPOSIT
    assert event.instrument is None
    assert event.quantity is None
    assert event.amount == Decimal("35000.0")


def test_no_net_amount_skipped(account: Account, source_file: Path) -> None:
    raw = _raw("Deposit", net_amount=None)
    event = normalize(raw, account, source_file)
    assert event is None


def test_bad_date_skipped(account: Account, source_file: Path) -> None:
    raw = _raw("Deposit", date_str="not-a-date")
    event = normalize(raw, account, source_file)
    assert event is None


def test_zero_commission_yields_zero_fees(account: Account, source_file: Path) -> None:
    raw = _raw("Deposit", commission=None)
    event = normalize(raw, account, source_file)
    assert event is not None
    assert event.fees == Decimal("0")
