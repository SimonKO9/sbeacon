from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from portfolio_tracker.domain.events import Event, EventType, SourceRef
from portfolio_tracker.domain.instruments import AssetClass, Instrument
from portfolio_tracker.reports.tax import (
    TAX_RATE,
    _is_regular,
    compute_tax_harvest,
    compute_tax_lots,
    compute_tax_summary,
)


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
    ts = ts or datetime(2025, 6, 1, tzinfo=UTC)
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


# ── wrapper classification ────────────────────────────────────────────────────

def test_regular_accounts_included() -> None:
    assert _is_regular("PLN")
    assert _is_regular("EUR")
    assert _is_regular("USD")
    assert _is_regular("pln")
    assert _is_regular("XTB_PLN")
    assert _is_regular("XTB_EUR")
    assert _is_regular("XTB_USD")
    assert _is_regular("IBKR_U25219041")


def test_ike_ikze_excluded() -> None:
    assert not _is_regular("IKE")
    assert not _is_regular("IKZE")
    assert not _is_regular("ike")
    assert not _is_regular("XTB_IKE")
    assert not _is_regular("XTB_IKZE")


# ── REGULAR-only filter ───────────────────────────────────────────────────────

def test_ike_lots_not_in_tax() -> None:
    events = [
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("s1", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2025, 6, 1, tzinfo=UTC)),
        # IKE buy and sell — must not appear in tax
        _event("b2", "IKE", EventType.TRADE, "-2000", symbol="A.US", quantity="20", price="100",
               ts=datetime(2025, 2, 1, tzinfo=UTC)),
        _event("s2", "IKE", EventType.TRADE, "3000", symbol="A.US", quantity="-20", price="150",
               ts=datetime(2025, 7, 1, tzinfo=UTC)),
    ]
    result = compute_tax_summary(events, fx_rates={}, year=2025)

    assert len(result.disposals) == 1
    assert result.disposals[0].gain_pln == Decimal("200")


# ── pool FIFO (cross-account within REGULAR) ──────────────────────────────────

def test_pool_fifo_across_regular_accounts() -> None:
    """PLN and EUR accounts holding the same ticker: matched as one pool."""
    events = [
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="B.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("b2", "EUR", EventType.TRADE, "-500", currency="EUR", symbol="B.US",
               quantity="5", price="100", ts=datetime(2025, 2, 1, tzinfo=UTC)),
        # Sell 10 shares from PLN account — closes the PLN buy first (FIFO)
        _event("s1", "PLN", EventType.TRADE, "1500", symbol="B.US", quantity="-10", price="150",
               ts=datetime(2025, 6, 1, tzinfo=UTC)),
    ]
    result = compute_tax_summary(events, fx_rates={"EUR": Decimal("4")}, year=2025)

    # Only the PLN lot is sold (10 shares, cost 100 PLN/share, proceeds 150 PLN/share)
    assert len(result.disposals) == 1
    assert result.disposals[0].quantity == Decimal("10")
    assert result.disposals[0].gain_pln == Decimal("500")  # (150-100) * 10


# ── year filter ───────────────────────────────────────────────────────────────

def test_year_filter_only_sells_in_year() -> None:
    events = [
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="C.US", quantity="10", price="100",
               ts=datetime(2024, 1, 1, tzinfo=UTC)),
        _event("s1", "PLN", EventType.TRADE, "1100", symbol="C.US", quantity="-10", price="110",
               ts=datetime(2024, 6, 1, tzinfo=UTC)),
        _event("b2", "PLN", EventType.TRADE, "-1000", symbol="C.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("s2", "PLN", EventType.TRADE, "1200", symbol="C.US", quantity="-10", price="120",
               ts=datetime(2025, 6, 1, tzinfo=UTC)),
    ]
    result_2024 = compute_tax_summary(events, fx_rates={}, year=2024)
    result_2025 = compute_tax_summary(events, fx_rates={}, year=2025)

    assert len(result_2024.disposals) == 1
    assert result_2024.net_gain_pln == Decimal("100")

    assert len(result_2025.disposals) == 1
    assert result_2025.net_gain_pln == Decimal("200")


# ── gain/loss calculation ────────────────────────────────────────────────────

def test_gain_pln_simple() -> None:
    events = [
        _event("b", "PLN", EventType.TRADE, "-3670", symbol="X.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("s", "PLN", EventType.TRADE, "5200", symbol="X.US", quantity="-10", price="130",
               ts=datetime(2025, 6, 1, tzinfo=UTC)),
    ]
    result = compute_tax_summary(events, fx_rates={}, year=2025)

    assert result.proceeds_pln == Decimal("5200")
    assert result.cost_basis_pln == Decimal("3670")
    assert result.net_gain_pln == Decimal("1530")
    assert result.tax_pln == result.net_gain_pln * TAX_RATE


def test_loss_gives_zero_tax() -> None:
    events = [
        _event("b", "PLN", EventType.TRADE, "-1000", symbol="Y.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("s", "PLN", EventType.TRADE, "800", symbol="Y.US", quantity="-10", price="80",
               ts=datetime(2025, 6, 1, tzinfo=UTC)),
    ]
    result = compute_tax_summary(events, fx_rates={}, year=2025)

    assert result.net_gain_pln == Decimal("-200")
    assert result.tax_pln == Decimal("0")


def test_multiple_disposals_aggregate() -> None:
    events = [
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("s1", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2025, 3, 1, tzinfo=UTC)),
        _event("b2", "PLN", EventType.TRADE, "-1000", symbol="B.US", quantity="5", price="200",
               ts=datetime(2025, 2, 1, tzinfo=UTC)),
        _event("s2", "PLN", EventType.TRADE, "900", symbol="B.US", quantity="-5", price="180",
               ts=datetime(2025, 5, 1, tzinfo=UTC)),
    ]
    result = compute_tax_summary(events, fx_rates={}, year=2025)

    assert len(result.disposals) == 2
    # A.US: +200, B.US: -100 → net = +100
    assert result.net_gain_pln == Decimal("100")
    assert result.tax_pln == Decimal("100") * TAX_RATE


# ── deductible costs ─────────────────────────────────────────────────────────

def test_fees_deducted_from_gain() -> None:
    events = [
        _event("b", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("s", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2025, 6, 1, tzinfo=UTC)),
        _event("fee1", "PLN", EventType.FEE, "-15",
               ts=datetime(2025, 3, 1, tzinfo=UTC)),
        _event("fee2", "PLN", EventType.FEE, "-10",
               ts=datetime(2025, 5, 1, tzinfo=UTC)),
    ]
    result = compute_tax_summary(events, fx_rates={}, year=2025)

    assert result.deductible_costs_pln == Decimal("25")
    assert result.net_gain_pln == Decimal("175")  # 200 - 25
    assert result.tax_pln == Decimal("175") * TAX_RATE


def test_ike_fees_not_deductible() -> None:
    events = [
        _event("b", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("s", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2025, 6, 1, tzinfo=UTC)),
        _event("fee", "IKE", EventType.FEE, "-20"),  # IKE fee — not deductible
    ]
    result = compute_tax_summary(events, fx_rates={}, year=2025)

    assert result.deductible_costs_pln == Decimal("0")
    assert result.net_gain_pln == Decimal("200")


def test_fees_outside_year_not_deductible() -> None:
    events = [
        _event("b", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("s", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2025, 6, 1, tzinfo=UTC)),
        _event("fee_old", "PLN", EventType.FEE, "-30",
               ts=datetime(2024, 12, 31, tzinfo=UTC)),  # previous year
    ]
    result = compute_tax_summary(events, fx_rates={}, year=2025)

    assert result.deductible_costs_pln == Decimal("0")


# ── FX conversion in estimate mode ───────────────────────────────────────────

def test_usd_account_gains_converted_to_pln() -> None:
    events = [
        _event("b", "USD", EventType.TRADE, "-1000", currency="USD", symbol="A.US",
               quantity="10", price="100", ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("s", "USD", EventType.TRADE, "1200", currency="USD", symbol="A.US",
               quantity="-10", price="120", ts=datetime(2025, 6, 1, tzinfo=UTC)),
    ]
    fx_rates = {"USD": Decimal("4")}
    result = compute_tax_summary(events, fx_rates=fx_rates, year=2025)

    # gain_usd = 200 USD → 800 PLN at rate 4
    assert result.net_gain_pln == Decimal("800")
    assert result.tax_pln == Decimal("800") * TAX_RATE


# ── tax lots ─────────────────────────────────────────────────────────────────

def test_tax_lots_shows_open_lots() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    events = [
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("b2", "PLN", EventType.TRADE, "-1500", symbol="A.US", quantity="10", price="150",
               ts=datetime(2025, 3, 1, tzinfo=UTC)),
        # Partial sell — closes first lot (FIFO)
        _event("s1", "PLN", EventType.TRADE, "1200", symbol="A.US", quantity="-10", price="120",
               ts=datetime(2025, 5, 1, tzinfo=UTC)),
    ]
    prices = {"A.US": Quote("A.US", Decimal("160"), "PLN", date(2025, 6, 1), "yahoo")}

    result = compute_tax_lots(events, "A.US", prices=prices, fx_rates={})

    # Only the second lot (bought at 150 PLN) should remain
    assert len(result.lots) == 1
    lot = result.lots[0]
    assert lot.quantity == Decimal("10")
    assert lot.cost_per_unit_pln == Decimal("150")
    assert lot.current_price_pln == Decimal("160")
    assert lot.unrealized_pln == Decimal("100")  # (160-150)*10


def test_tax_lots_ike_excluded() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    events = [
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="A.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("b2", "IKE", EventType.TRADE, "-2000", symbol="A.US", quantity="20", price="100",
               ts=datetime(2025, 2, 1, tzinfo=UTC)),
    ]
    prices = {"A.US": Quote("A.US", Decimal("120"), "PLN", date(2025, 6, 1), "yahoo")}

    result = compute_tax_lots(events, "A.US", prices=prices, fx_rates={})

    # Only PLN lot should appear
    assert len(result.lots) == 1
    assert result.lots[0].quantity == Decimal("10")


# ── harvest ──────────────────────────────────────────────────────────────────

def test_harvest_loss_candidates_ranked() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    events = [
        # Profitable position (gain) — must not appear as candidate
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="WIN.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        # Losing position 1
        _event("b2", "PLN", EventType.TRADE, "-2000", symbol="LOSE1.US", quantity="10", price="200",
               ts=datetime(2025, 2, 1, tzinfo=UTC)),
        # Losing position 2 (bigger loss)
        _event("b3", "PLN", EventType.TRADE, "-3000", symbol="LOSE2.US", quantity="10", price="300",
               ts=datetime(2025, 3, 1, tzinfo=UTC)),
    ]
    prices = {
        "WIN.US": Quote("WIN.US", Decimal("150"), "PLN", date(2025, 6, 1), "yahoo"),
        "LOSE1.US": Quote("LOSE1.US", Decimal("150"), "PLN", date(2025, 6, 1), "yahoo"),  # -500 loss
        "LOSE2.US": Quote("LOSE2.US", Decimal("200"), "PLN", date(2025, 6, 1), "yahoo"),  # -1000 loss
    }
    result = compute_tax_harvest(events, prices=prices, fx_rates={}, year=2025)

    assert len(result.candidates) == 2
    # Most negative first
    assert result.candidates[0].symbol == "LOSE2.US"
    assert result.candidates[0].fifo_loss_pln == Decimal("-1000")
    assert result.candidates[1].symbol == "LOSE1.US"
    assert result.candidates[1].fifo_loss_pln == Decimal("-500")


def test_harvest_cumulative_offset() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    events = [
        _event("b1", "PLN", EventType.TRADE, "-1000", symbol="L1.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
        _event("b2", "PLN", EventType.TRADE, "-1000", symbol="L2.US", quantity="10", price="100",
               ts=datetime(2025, 2, 1, tzinfo=UTC)),
    ]
    prices = {
        "L1.US": Quote("L1.US", Decimal("70"), "PLN", date(2025, 6, 1), "yahoo"),   # -300
        "L2.US": Quote("L2.US", Decimal("80"), "PLN", date(2025, 6, 1), "yahoo"),   # -200
    }
    result = compute_tax_harvest(events, prices=prices, fx_rates={}, year=2025)

    # sorted: L1 (-300) then L2 (-200)
    assert result.candidates[0].cumulative_offset_pln == Decimal("-300")
    assert result.candidates[1].cumulative_offset_pln == Decimal("-500")


def test_harvest_no_candidates_when_all_profitable() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    events = [
        _event("b", "PLN", EventType.TRADE, "-1000", symbol="G.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
    ]
    prices = {"G.US": Quote("G.US", Decimal("150"), "PLN", date(2025, 6, 1), "yahoo")}

    result = compute_tax_harvest(events, prices=prices, fx_rates={}, year=2025)

    assert result.candidates == []


def test_harvest_tax_saved() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    events = [
        _event("b", "PLN", EventType.TRADE, "-1000", symbol="L.US", quantity="10", price="100",
               ts=datetime(2025, 1, 1, tzinfo=UTC)),
    ]
    prices = {"L.US": Quote("L.US", Decimal("50"), "PLN", date(2025, 6, 1), "yahoo")}  # -500 loss

    result = compute_tax_harvest(events, prices=prices, fx_rates={}, year=2025)

    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.fifo_loss_pln == Decimal("-500")
    assert c.tax_saved_pln == Decimal("500") * TAX_RATE


def test_harvest_estimate_flag_passed() -> None:
    result = compute_tax_harvest([], prices={}, fx_rates={}, year=2025, estimate=True)
    assert result.estimate is True
