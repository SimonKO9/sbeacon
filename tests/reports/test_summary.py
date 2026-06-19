from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from portfolio_tracker.domain.events import Event, EventType, SourceRef
from portfolio_tracker.domain.instruments import AssetClass, Instrument
from portfolio_tracker.reports.summary import _is_external_deposit, _xirr, compute_summary


def _src(comment: str = "") -> SourceRef:
    return SourceRef(file=Path("f.xlsx"), sheet="Cash operations", row=1, raw={"Comment": comment})


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
    comment: str = "",
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
        source=_src(comment),
        instrument=instrument,
        quantity=Decimal(quantity) if quantity else None,
        price=Decimal(price) if price else None,
    )


def test_is_external_deposit() -> None:
    assert _is_external_deposit(_event("e", "PLN", EventType.DEPOSIT, "100"))
    assert not _is_external_deposit(_event("e", "PLN", EventType.TRANSFER, "100"))
    assert not _is_external_deposit(_event("e", "PLN", EventType.INTEREST, "5"))


def test_xirr_simple() -> None:
    # -100 today, +110 in a year → ~10% p.a.
    cfs = [(date(2024, 1, 1), -100.0), (date(2025, 1, 1), 110.0)]
    result = _xirr(cfs)
    assert result is not None
    assert abs(result - 0.10) < 0.01


def test_xirr_insufficient_data() -> None:
    assert _xirr([]) is None
    assert _xirr([(date(2024, 1, 1), -100.0)]) is None


def test_compute_summary_simple() -> None:
    """Deposit + buy + current price → correct value, P/L, and unrealized."""
    from portfolio_tracker.pricing.provider import Quote

    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "10000"),
        _event("buy", "PLN", EventType.TRADE, "-3670",
               symbol="AAPL.US", quantity="10", price="100",
               ts=datetime(2024, 1, 2, tzinfo=UTC)),
    ]
    prices = {"AAPL.US": Quote("AAPL.US", Decimal("130"), "USD", date(2024, 6, 1), "yahoo")}
    fx_rates = {"USD": Decimal("4")}

    result = compute_summary(events, prices=prices, fx_rates=fx_rates)

    # cash = 10000 - 3670 = 6330 PLN
    # market value = 10 * 130 * 4 = 5200 PLN
    # total = 11530 PLN; net_in = 10000; P/L = +1530
    assert result.total.cash_pln == Decimal("6330")
    assert result.total.market_value_pln == Decimal("5200")
    assert result.total.total_value_pln == Decimal("11530")
    assert result.total.net_in_pln == Decimal("10000")
    assert result.total.pnl_pln == Decimal("1530")
    assert result.total.pnl_pct is not None
    assert abs(result.total.pnl_pct - Decimal("15.3")) < Decimal("0.1")


def test_income_and_expenses_in_decomposition() -> None:
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "10000"),
        _event("div", "PLN", EventType.DIVIDEND, "200"),
        _event("fee", "PLN", EventType.FEE, "-15"),
        _event("tax", "PLN", EventType.TAX, "-38"),
    ]
    result = compute_summary(events, prices={}, fx_rates={})

    assert result.decomposition.dividends_pln == Decimal("200")
    assert result.decomposition.fees_pln == Decimal("-15")
    assert result.decomposition.taxes_pln == Decimal("-38")


def test_internal_transfer_not_counted_in_global_net_in() -> None:
    """IKE deposit shows up per-account but not in global net in."""
    events = [
        _event("dep", "PLN", EventType.DEPOSIT, "10000"),
        _event("out", "PLN", EventType.TRANSFER, "-5000"),
        _event("in_", "IKE", EventType.TRANSFER, "5000", currency="PLN"),
    ]
    result = compute_summary(events, prices={}, fx_rates={})

    assert result.total.net_in_pln == Decimal("10000")
    assert result.total.cash_pln == Decimal("10000")  # internal transfer nets out

    by_account = {r.account_id: r for r in result.account_rows}
    assert by_account["PLN"].net_in_pln == Decimal("5000")   # 10000 deposit − 5000 outbound transfer
    assert by_account["IKE"].net_in_pln == Decimal("5000")
