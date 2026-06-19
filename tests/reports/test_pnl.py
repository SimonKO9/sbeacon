from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_tracker.domain.events import Event, EventType, SourceRef
from portfolio_tracker.domain.instruments import AssetClass, Instrument
from portfolio_tracker.reports.pnl import compute_pnl


def _src() -> SourceRef:
    return SourceRef(file=Path("f.xlsx"), sheet="Cash operations", row=1, raw={})


def _event(
    eid: str,
    account_id: str,
    type_: EventType,
    amount: str,
    currency: str = "PLN",
    ts: datetime | None = None,
    symbol: str | None = None,
    quantity: str | None = None,
    price: str | None = None,
) -> Event:
    ts = ts or datetime(2024, 1, 1, tzinfo=UTC)
    instrument = Instrument(symbol=symbol, asset_class=AssetClass.EQUITY) if symbol else None
    return Event(
        id=eid,
        account_id=account_id,
        timestamp=ts,
        type=type_,
        amount=Decimal(amount),
        currency=currency,
        source=_src(),
        instrument=instrument,
        quantity=Decimal(quantity) if quantity else None,
        price=Decimal(price) if price else None,
    )


# ── realized P/L ──────────────────────────────────────────────────────────────

def test_realized_pnl_by_instrument() -> None:
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "1000"),
        _event("buy", "PLN", EventType.TRADE, "-1000", symbol="AAPL.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
        _event("sell", "PLN", EventType.TRADE, "1200", symbol="AAPL.US", quantity="-10", price="120",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
    ]
    result = compute_pnl(events, prices={}, fx_rates={})

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.group_key == "AAPL.US"
    assert row.realized_pln == Decimal("200")
    assert row.unrealized_pln == Decimal("0")
    assert row.cost_basis_closed_pln == Decimal("1000")
    assert row.realized_return_pct is not None
    assert abs(row.realized_return_pct - Decimal("20")) < Decimal("0.01")


def test_realized_pnl_loss() -> None:
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "1000"),
        _event("buy", "PLN", EventType.TRADE, "-1000", symbol="XYZ.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
        _event("sell", "PLN", EventType.TRADE, "800", symbol="XYZ.US", quantity="-10", price="80",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
    ]
    result = compute_pnl(events, prices={}, fx_rates={})

    row = result.rows[0]
    assert row.realized_pln == Decimal("-200")
    assert row.realized_return_pct is not None
    assert abs(row.realized_return_pct - Decimal("-20")) < Decimal("0.01")


# ── unrealized P/L ────────────────────────────────────────────────────────────

def test_unrealized_pnl_by_instrument() -> None:
    from portfolio_tracker.pricing.provider import Quote
    from datetime import date

    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "1000"),
        _event("buy", "PLN", EventType.TRADE, "-1000", symbol="AAPL.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
    ]
    prices = {"AAPL.US": Quote("AAPL.US", Decimal("130"), "PLN", date(2024, 6, 1), "yahoo")}
    result = compute_pnl(events, prices=prices, fx_rates={})

    row = result.rows[0]
    assert row.group_key == "AAPL.US"
    assert row.unrealized_pln == Decimal("300")
    assert row.realized_pln == Decimal("0")
    assert row.cost_basis_open_pln == Decimal("1000")
    assert row.unrealized_return_pct is not None
    assert abs(row.unrealized_return_pct - Decimal("30")) < Decimal("0.01")


def test_unrealized_uses_fx_rate() -> None:
    from portfolio_tracker.pricing.provider import Quote
    from datetime import date

    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "3700"),
        _event("buy", "PLN", EventType.TRADE, "-3700", symbol="AAPL.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
    ]
    prices = {"AAPL.US": Quote("AAPL.US", Decimal("110"), "USD", date(2024, 6, 1), "yahoo")}
    fx_rates = {"USD": Decimal("4")}
    result = compute_pnl(events, prices=prices, fx_rates=fx_rates)

    row = result.rows[0]
    # market value = 10 * 110 * 4 = 4400; cost basis = 3700; unrealized = 700
    assert row.unrealized_pln == Decimal("700")


# ── period filtering ──────────────────────────────────────────────────────────

def test_period_filter_realized() -> None:
    from datetime import date

    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "2000"),
        _event("buy1", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2023, 1, 1, tzinfo=UTC)),
        _event("sell1", "PLN", EventType.TRADE, "1100", symbol="A.US", quantity="-10", price="110",
               ts=datetime(2023, 6, 1, tzinfo=UTC)),
        _event("buy2", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
        _event("sell2", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
    ]
    result = compute_pnl(
        events, prices={}, fx_rates={},
        date_from=date(2024, 1, 1), date_to=date(2024, 12, 31),
    )

    assert result.period_label == "2024"
    # Only the 2024 sell (+200) should be included; 2023 sell (+100) excluded
    row = result.rows[0]
    assert row.realized_pln == Decimal("200")


def test_period_filter_unrealized_always_current() -> None:
    """Even with a period filter, unrealized is always the current snapshot."""
    from portfolio_tracker.pricing.provider import Quote
    from datetime import date

    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "1000"),
        # Buy in 2022 — outside any period we might filter
        _event("buy", "PLN", EventType.TRADE, "-1000", symbol="B.US", quantity="10", price="100",
               ts=datetime(2022, 1, 1, tzinfo=UTC)),
    ]
    prices = {"B.US": Quote("B.US", Decimal("150"), "PLN", date(2024, 6, 1), "yahoo")}
    result = compute_pnl(
        events, prices=prices, fx_rates={},
        date_from=date(2024, 1, 1), date_to=date(2024, 12, 31),
    )

    # Unrealized must appear even though the buy was in 2022
    row = result.rows[0]
    assert row.unrealized_pln == Decimal("500")


# ── income ────────────────────────────────────────────────────────────────────

def test_dividends_by_instrument() -> None:
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "1000"),
        _event("buy", "PLN", EventType.TRADE, "-1000", symbol="D.US", quantity="10", price="100"),
        _event("div", "PLN", EventType.DIVIDEND, "50", symbol="D.US"),
    ]
    result = compute_pnl(events, prices={}, fx_rates={})

    rows_by_key = {r.group_key: r for r in result.rows}
    assert "D.US" in rows_by_key
    assert rows_by_key["D.US"].dividends_pln == Decimal("50")
    assert rows_by_key["D.US"].income_pln == Decimal("50")


def test_interest_no_instrument_goes_to_portfolio_row() -> None:
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "1000"),
        _event("int", "PLN", EventType.INTEREST, "10"),
    ]
    result = compute_pnl(events, prices={}, fx_rates={})

    rows_by_key = {r.group_key: r for r in result.rows}
    assert "Portfolio" in rows_by_key
    assert rows_by_key["Portfolio"].interest_pln == Decimal("10")


def test_fees_and_taxes() -> None:
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "1000"),
        _event("fee", "PLN", EventType.FEE, "-5"),
        _event("tax", "PLN", EventType.TAX, "-19"),
    ]
    result = compute_pnl(events, prices={}, fx_rates={})

    rows_by_key = {r.group_key: r for r in result.rows}
    assert rows_by_key["Portfolio"].fees_pln == Decimal("-5")
    assert rows_by_key["Portfolio"].taxes_pln == Decimal("-19")
    assert rows_by_key["Portfolio"].total_pln == Decimal("-24")


# ── grouping ──────────────────────────────────────────────────────────────────

def test_by_account() -> None:
    events = [
        _event("dep1", "PLN", EventType.DEPOSIT, "5000"),
        _event("buy1", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100"),
        _event("sell1", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
        _event("dep2", "IKE", EventType.DEPOSIT, "5000"),
        _event("buy2", "IKE", EventType.TRADE, "-2000", symbol="B.US", quantity="20", price="100",
               ts=datetime(2024, 2, 1, tzinfo=UTC)),
        _event("sell2", "IKE", EventType.TRADE, "1800", symbol="B.US", quantity="-20", price="90",
               ts=datetime(2024, 7, 1, tzinfo=UTC)),
    ]
    result = compute_pnl(events, prices={}, fx_rates={}, by="account")

    rows_by_key = {r.group_key: r for r in result.rows}
    assert "PLN" in rows_by_key
    assert "IKE" in rows_by_key
    assert rows_by_key["PLN"].realized_pln == Decimal("200")
    assert rows_by_key["IKE"].realized_pln == Decimal("-200")


def test_by_wrapper() -> None:
    events = [
        _event("dep1", "PLN", EventType.DEPOSIT, "1000"),
        _event("buy1", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100"),
        _event("sell1", "PLN", EventType.TRADE, "1100", symbol="A.US", quantity="-10", price="110",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
        _event("dep2", "IKE", EventType.DEPOSIT, "2000"),
        _event("buy2", "IKE", EventType.TRADE, "-2000", symbol="B.US", quantity="20", price="100",
               ts=datetime(2024, 2, 1, tzinfo=UTC)),
        _event("sell2", "IKE", EventType.TRADE, "2200", symbol="B.US", quantity="-20", price="110",
               ts=datetime(2024, 7, 1, tzinfo=UTC)),
    ]
    result = compute_pnl(events, prices={}, fx_rates={}, by="wrapper")

    rows_by_key = {r.group_key: r for r in result.rows}
    assert rows_by_key["REGULAR"].realized_pln == Decimal("100")
    assert rows_by_key["IKE"].realized_pln == Decimal("200")


# ── totals and reconciliation ─────────────────────────────────────────────────

def test_total_row_sums_all_components() -> None:
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "2000"),
        _event("buy", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
        _event("sell", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
        _event("div", "PLN", EventType.DIVIDEND, "50", symbol="A.US"),
        _event("fee", "PLN", EventType.FEE, "-10"),
    ]
    result = compute_pnl(events, prices={}, fx_rates={})

    expected_total = Decimal("200") + Decimal("50") + Decimal("-10")
    assert result.total.total_pln == expected_total


def test_fx_cash_residual_lifetime_no_prices() -> None:
    """With no open positions and no FX, fx_cash should be 0."""
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "1000"),
        _event("buy", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
        _event("sell", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
    ]
    result = compute_pnl(events, prices={}, fx_rates={})

    # no open positions, all PLN → no FX residual
    # global_pnl = cash(1000 - 1000 + 1200) - net_in(1000) = 1200 - 1000 = 200
    # components = realized(200)
    # fx_cash = 200 - 200 = 0
    assert result.fx_cash_pln == Decimal("0")
    assert result.period_label == "lifetime"


def test_period_label_year() -> None:
    from datetime import date

    result = compute_pnl(
        [], prices={}, fx_rates={},
        date_from=date(2025, 1, 1), date_to=date(2025, 12, 31),
    )
    assert result.period_label == "2025"


def test_period_label_custom_range() -> None:
    from datetime import date

    result = compute_pnl(
        [], prices={}, fx_rates={},
        date_from=date(2025, 3, 1), date_to=date(2025, 9, 30),
    )
    assert "2025-03-01" in result.period_label
    assert "2025-09-30" in result.period_label


def test_sort_by_realized() -> None:
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "3000"),
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
        _event("s1", "PLN", EventType.TRADE, "1500", symbol="A.US", quantity="-10", price="150",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
        _event("b2", "PLN", EventType.TRADE, "-1000", symbol="B.US", quantity="10", price="100",
               ts=datetime(2024, 2, 1, tzinfo=UTC)),
        _event("s2", "PLN", EventType.TRADE, "1100", symbol="B.US", quantity="-10", price="110",
               ts=datetime(2024, 7, 1, tzinfo=UTC)),
    ]
    result = compute_pnl(events, prices={}, fx_rates={}, sort_by="realized")

    assert result.rows[0].group_key == "A.US"
    assert result.rows[0].realized_pln == Decimal("500")


def test_top_not_filtered_by_compute() -> None:
    """compute_pnl returns all rows; CLI applies --top slicing."""
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "3000"),
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
        _event("s1", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
        _event("b2", "PLN", EventType.TRADE, "-1000", symbol="B.US", quantity="10", price="100",
               ts=datetime(2024, 2, 1, tzinfo=UTC)),
        _event("s2", "PLN", EventType.TRADE, "1300", symbol="B.US", quantity="-10", price="130",
               ts=datetime(2024, 7, 1, tzinfo=UTC)),
    ]
    result = compute_pnl(events, prices={}, fx_rates={})
    assert len(result.rows) == 2
