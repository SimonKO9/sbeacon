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
    assert posns[0].cost_currency == "PLN"  # account base currency


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


def test_prices_fill_market_value_and_pnl() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    # USD account: cost and quote are both USD → native unrealized_pnl is meaningful
    events = [_trade("e1", "AAPL.US", "10", "100", T1, currency="USD")]
    prices = {
        "AAPL.US": Quote(
            symbol="AAPL.US",
            price=Decimal("120"),
            currency="USD",
            as_of=date(2024, 6, 1),
            source="yahoo",
        )
    }
    posns = compute_positions(events, prices=prices)
    assert posns[0].current_price == Decimal("120")
    assert posns[0].market_value == Decimal("1200")
    assert posns[0].unrealized_pnl == Decimal("200")  # (120 - 100) * 10


def test_no_prices_leaves_fields_none() -> None:
    events = [_trade("e1", "AAPL.US", "10", "100", T1)]
    posns = compute_positions(events)
    assert posns[0].current_price is None
    assert posns[0].market_value is None
    assert posns[0].unrealized_pnl is None
    assert posns[0].market_value_pln is None
    assert posns[0].weight_pct is None


def test_fx_rates_fill_pln_fields() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    # USD account: amount = qty * price in USD, currency = "USD"
    events = [_trade("e1", "AAPL.US", "10", "100", T1, currency="USD")]
    prices = {"AAPL.US": Quote(symbol="AAPL.US", price=Decimal("120"), currency="USD", as_of=date(2024, 6, 1), source="yahoo")}
    fx_rates = {"USD": Decimal("4")}
    posns = compute_positions(events, prices=prices, fx_rates=fx_rates)
    assert posns[0].market_value_pln == Decimal("4800")   # 10 * 120 * 4
    assert posns[0].unrealized_pnl_pln == Decimal("800")  # (120 - 100) * 10 * 4
    assert posns[0].weight_pct == Decimal("100")


def test_weight_pct_across_positions() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    events = [
        _trade("e1", "AAPL.US", "10", "100", T1),
        _trade("e2", "MSFT.US", "5", "200", T1),
    ]
    prices = {
        "AAPL.US": Quote(symbol="AAPL.US", price=Decimal("100"), currency="USD", as_of=date(2024, 6, 1), source="yahoo"),
        "MSFT.US": Quote(symbol="MSFT.US", price=Decimal("200"), currency="USD", as_of=date(2024, 6, 1), source="yahoo"),
    }
    fx_rates = {"USD": Decimal("1")}
    posns = compute_positions(events, prices=prices, fx_rates=fx_rates)
    # market values: AAPL=1000, MSFT=1000 → 50/50
    by_symbol = {p.symbol: p for p in posns}
    assert by_symbol["AAPL.US"].weight_pct == Decimal("50")
    assert by_symbol["MSFT.US"].weight_pct == Decimal("50")


def test_sorted_by_market_value_pln_descending() -> None:
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    events = [
        _trade("e1", "AAPL.US", "1", "100", T1),
        _trade("e2", "MSFT.US", "1", "500", T1),
    ]
    prices = {
        "AAPL.US": Quote(symbol="AAPL.US", price=Decimal("100"), currency="USD", as_of=date(2024, 6, 1), source="yahoo"),
        "MSFT.US": Quote(symbol="MSFT.US", price=Decimal("500"), currency="USD", as_of=date(2024, 6, 1), source="yahoo"),
    }
    posns = compute_positions(events, prices=prices, fx_rates={"USD": Decimal("1")})
    assert posns[0].symbol == "MSFT.US"
    assert posns[1].symbol == "AAPL.US"


def test_pln_cost_foreign_proxy_converts_native_fields() -> None:
    """LYPS.PL: avg_cost in PLN (Warsaw), current_price from LYPS.DE (EUR proxy).
    Native fields must be converted to PLN so the positions table shows meaningful values.
    """
    from datetime import date
    from portfolio_tracker.pricing.provider import Quote

    # avg_cost = 246.60 PLN (XTB Warsaw price), 35 shares → total cost = 8,631 PLN
    events = [_trade("e1", "LYPS.PL", "35", "246.60", T1)]
    # Yahoo resolves LYPS.PL → LYPS.DE → EUR 66.19
    prices = {"LYPS.PL": Quote(symbol="LYPS.PL", price=Decimal("66.19"), currency="EUR", as_of=date(2024, 6, 1), source="yahoo")}
    fx_rates = {"EUR": Decimal("4.25")}

    posns = compute_positions(events, prices=prices, fx_rates=fx_rates)
    pos = posns[0]

    assert pos.cost_currency == "PLN"
    # quote_currency promoted to PLN after conversion
    assert pos.quote_currency == "PLN"
    # current_price converted: 66.19 * 4.25 = 281.3075
    assert pos.current_price == Decimal("66.19") * Decimal("4.25")
    # market_value_pln = market_value = 35 * 66.19 * 4.25 = 9,843.2...
    assert pos.market_value is not None
    assert pos.market_value == pos.market_value_pln
    # unrealized_pnl = unrealized_pnl_pln = 9,843.2 - 8,631 > 0
    assert pos.unrealized_pnl is not None
    assert pos.unrealized_pnl == pos.unrealized_pnl_pln
    assert pos.unrealized_pnl > 0


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
