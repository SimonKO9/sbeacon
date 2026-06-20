from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from portfolio_tracker.config import AppConfig, ExtraAsset
from portfolio_tracker.domain.instruments import AssetClass, Role
from portfolio_tracker.reports.allocation import (
    AllocationResult,
    TaggedInstrument,
    _auto_tag,
    compute_allocation,
    tag_instrument,
)


# ── _auto_tag ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,name,expected_ac,expected_role,expected_src", [
    # crypto via name
    ("XXBT.DE", "Galaxy Physical Bitcoin", AssetClass.CRYPTO, Role.CRYPTO, "auto"),
    # crypto via ticker keyword
    ("XXBT.DE", None, AssetClass.CRYPTO, Role.CRYPTO, "auto"),
    # commodity via name
    ("ETCGLD.PL", "Invesco Physical Gold ETC", AssetClass.COMMODITY, Role.REAL_ASSETS, "auto"),
    # fixed income via name
    ("IBTU.DE", "iShares Core Global Aggregate Bond UCITS ETF", AssetClass.FIXED_INCOME, Role.FIXED_INCOME, "auto"),
    # broad-market equity ETF → core (auto)
    ("LYPS.PL", "Lyxor S&P 500 UCITS ETF", AssetClass.EQUITY, Role.CORE, "auto"),
    # broad MSCI World
    ("SWDA.DE", "iShares Core MSCI World UCITS ETF", AssetClass.EQUITY, Role.CORE, "auto"),
    # sector ETF → thematic + review
    ("HEAL.PL", "iShares Healthcare UCITS ETF", AssetClass.EQUITY, Role.THEMATIC, "review"),
    # individual stock → satellite
    ("NOW.US", "ServiceNow Inc", AssetClass.EQUITY, Role.SATELLITE, "auto"),
    # stock with no name
    ("CDR.PL", None, AssetClass.EQUITY, Role.SATELLITE, "auto"),
    # Nasdaq broad ETF → core
    ("CNDX.PL", "iShares Nasdaq 100 UCITS ETF", AssetClass.EQUITY, Role.CORE, "auto"),
])
def test_auto_tag(ticker, name, expected_ac, expected_role, expected_src):
    ac, role, src = _auto_tag(ticker, name)
    assert ac == expected_ac
    assert role == expected_role
    assert src == expected_src


def test_auto_tag_empty_is_untagged():
    ac, role, src = _auto_tag("", None)
    assert ac is None
    assert role is None
    assert src == "untagged"


# ── tag_instrument ─────────────────────────────────────────────────────────────

def test_tag_instrument_no_override():
    t = tag_instrument("NOW.US", "ServiceNow", {})
    assert t.asset_class == AssetClass.EQUITY
    assert t.role == Role.SATELLITE
    assert t.source == "auto"


def test_tag_instrument_manual_role_override():
    t = tag_instrument("HEAL.PL", "Healthcare ETF", {"role": "thematic"})
    assert t.role == Role.THEMATIC
    assert t.source == "manual"


def test_tag_instrument_manual_ac_override():
    t = tag_instrument("SOMEETF.DE", "Unknown ETF", {"asset_class": "fixed-income"})
    assert t.asset_class == AssetClass.FIXED_INCOME
    assert t.source == "manual"


def test_tag_instrument_both_overrides():
    t = tag_instrument("FOO.PL", None, {"role": "core", "asset_class": "equity"})
    assert t.role == Role.CORE
    assert t.asset_class == AssetClass.EQUITY
    assert t.source == "manual"


def test_tag_instrument_partial_override_keeps_auto():
    # Only role overridden; asset_class stays from auto
    t = tag_instrument("LYPS.PL", "Lyxor S&P 500 UCITS ETF", {"role": "core"})
    assert t.role == Role.CORE
    assert t.asset_class == AssetClass.EQUITY
    assert t.source == "manual"


# ── compute_allocation ─────────────────────────────────────────────────────────

def _make_events():
    """Minimal set of events: one deposit + two trades."""
    from portfolio_tracker.domain.events import Event, EventType, SourceRef
    from portfolio_tracker.domain.instruments import Instrument

    src = SourceRef(file="x.xlsx", sheet="Closed Positions", row=1)

    deposit = Event(
        id="dep1", account_id="XTB_PLN",
        timestamp=dt.datetime(2024, 1, 1),
        type=EventType.DEPOSIT,
        amount=Decimal("10000"), currency="PLN", source=src,
    )
    # Buy 10 shares of S&P 500 ETF @ 100 PLN
    buy_sp500 = Event(
        id="t1", account_id="XTB_PLN",
        timestamp=dt.datetime(2024, 1, 2),
        type=EventType.TRADE,
        amount=Decimal("-1000"), currency="PLN", source=src,
        instrument=Instrument(symbol="LYPS.PL", name="Lyxor S&P 500 UCITS ETF"),
        quantity=Decimal("10"), price=Decimal("100"),
    )
    # Buy 5 shares of stock @ 200 PLN
    buy_stock = Event(
        id="t2", account_id="XTB_PLN",
        timestamp=dt.datetime(2024, 1, 3),
        type=EventType.TRADE,
        amount=Decimal("-1000"), currency="PLN", source=src,
        instrument=Instrument(symbol="NOW.US", name="ServiceNow"),
        quantity=Decimal("5"), price=Decimal("200"),
    )
    return [deposit, buy_sp500, buy_stock]


def _make_prices():
    from portfolio_tracker.pricing.provider import Quote

    return {
        "LYPS.PL": Quote(symbol="LYPS.PL", price=Decimal("110"), currency="PLN", as_of=dt.date.today(), source="test"),
        "NOW.US": Quote(symbol="NOW.US", price=Decimal("220"), currency="PLN", as_of=dt.date.today(), source="test"),
    }


def test_compute_allocation_by_role_basic():
    events = _make_events()
    prices = _make_prices()
    fx = {}
    cfg = AppConfig()

    result = compute_allocation(events, prices=prices, fx_rates=fx, config=cfg, by="role")

    assert isinstance(result, AllocationResult)
    assert result.lens == "role"
    # LYPS.PL: 10 * 110 = 1100 → core
    # NOW.US:  5 * 220 = 1100 → satellite
    # cash: 10000 - 1000 - 1000 = 8000
    total_expected = Decimal("1100") + Decimal("1100") + Decimal("8000")
    assert result.total_pln == total_expected

    by_bucket = {r.bucket: r for r in result.rows}
    assert by_bucket["core"].value_pln == Decimal("1100")
    assert by_bucket["satellite"].value_pln == Decimal("1100")
    assert by_bucket["cash"].value_pln == Decimal("8000")


def test_compute_allocation_by_role_weights_sum_to_one():
    events = _make_events()
    prices = _make_prices()
    cfg = AppConfig()

    result = compute_allocation(events, prices=prices, fx_rates={}, config=cfg, by="role")

    weight_sum = sum(r.weight for r in result.rows)
    assert abs(weight_sum - Decimal("1")) < Decimal("0.001")


def test_compute_allocation_ex_cash():
    events = _make_events()
    prices = _make_prices()
    cfg = AppConfig()

    result = compute_allocation(
        events, prices=prices, fx_rates={}, config=cfg, by="role", ex_cash=True
    )

    by_bucket = {r.bucket: r for r in result.rows}
    assert "cash" not in by_bucket or by_bucket["cash"].value_pln == Decimal("0")
    assert result.total_pln == Decimal("2200")  # 1100 + 1100


def test_compute_allocation_by_asset_class():
    events = _make_events()
    prices = _make_prices()
    cfg = AppConfig()

    result = compute_allocation(events, prices=prices, fx_rates={}, config=cfg, by="asset-class")

    assert result.lens == "asset-class"
    by_bucket = {r.bucket: r for r in result.rows}
    assert by_bucket["equity"].value_pln == Decimal("2200")
    assert by_bucket["cash"].value_pln == Decimal("8000")
    # no targets for asset-class
    assert all(r.target is None for r in result.rows)


def test_compute_allocation_manual_override():
    events = _make_events()
    prices = _make_prices()
    # Override LYPS.PL to be thematic instead of core
    cfg = AppConfig(instruments={"LYPS.PL": {"role": "thematic"}})

    result = compute_allocation(events, prices=prices, fx_rates={}, config=cfg, by="role")

    by_bucket = {r.bucket: r for r in result.rows}
    assert by_bucket["thematic"].value_pln == Decimal("1100")
    assert by_bucket["core"].value_pln == Decimal("0")


def test_compute_allocation_extra_assets_excluded():
    """real-assets and reserves are excluded from the liquid allocation view."""
    events = _make_events()
    prices = _make_prices()
    cfg = AppConfig(extra_assets={
        "real-assets": [ExtraAsset(name="Apartment", value=Decimal("500000"), currency="PLN")],
        "reserves": [ExtraAsset(name="Emergency fund", value=Decimal("30000"), currency="PLN")],
    })
    liquid_total = Decimal("1100") + Decimal("1100") + Decimal("8000")

    for lens in ("role", "asset-class"):
        result = compute_allocation(events, prices=prices, fx_rates={}, config=cfg, by=lens)
        assert result.total_pln == liquid_total, f"lens={lens}: extra-assets leaked into total"
        by_bucket = {r.bucket: r for r in result.rows}
        assert by_bucket.get("real-assets") is None or by_bucket["real-assets"].value_pln == Decimal("0")
        assert by_bucket.get("real-estate") is None or by_bucket["real-estate"].value_pln == Decimal("0")
        assert by_bucket.get("reserves") is None


def test_compute_allocation_drift_and_rebalance():
    events = _make_events()
    prices = _make_prices()
    # Simple: 100% in core, 0 target cash → big drift
    cfg = AppConfig(targets={"core": 1.0, "satellite": 0.0, "thematic": 0.0,
                              "real-assets": 0.0, "crypto": 0.0,
                              "fixed-income": 0.0, "cash": 0.0})

    result = compute_allocation(events, prices=prices, fx_rates={}, config=cfg, by="role")

    by_bucket = {r.bucket: r for r in result.rows}
    core_row = by_bucket["core"]
    assert core_row.target is not None
    assert core_row.drift is not None
    # drift = weight - target
    assert abs(core_row.drift - (core_row.weight - core_row.target)) < Decimal("0.0001")
    # rebalance = (target - weight) * total
    assert abs(core_row.rebalance - (core_row.target - core_row.weight) * result.total_pln) < Decimal("1")
