from __future__ import annotations

from decimal import Decimal

from portfolio_tracker.adapters.xtb.parse import parse_comment


def test_open_buy() -> None:
    r = parse_comment("Stock purchase", "OPEN BUY 3 @ 102.95")
    assert r["action"] == "OPEN"
    assert r["side"] == "BUY"
    assert r["quantity"] == Decimal("3")
    assert r["price"] == Decimal("102.95")


def test_close_buy_fractional_qty() -> None:
    r = parse_comment("Stock sell", "CLOSE BUY 47/304 @ 10.8400")
    assert r["action"] == "CLOSE"
    assert r["side"] == "BUY"
    assert r["quantity"] == Decimal("47")
    assert r["price"] == Decimal("10.8400")


def test_open_sell() -> None:
    r = parse_comment("Stock sell", "OPEN SELL 10 @ 55.00")
    assert r["action"] == "OPEN"
    assert r["side"] == "SELL"
    assert r["quantity"] == Decimal("10")


def test_correction_row_flagged() -> None:
    r = parse_comment("Stock purchase", "Corr OPEN BUY 3 @ 102.95")
    assert r.get("is_correction") is True


def test_transfer_fx_rate() -> None:
    r = parse_comment("Transfer", "PLN to USD ... Exchange rate:4.0123")
    assert r["fx_rate"] == Decimal("4.0123")


def test_transfer_fx_rate_space() -> None:
    r = parse_comment("Transfer", "Exchange rate 3.9876")
    assert r["fx_rate"] == Decimal("3.9876")


def test_external_deposit_blik() -> None:
    r = parse_comment("Deposit", "Adyen BLIK 1000 PLN")
    assert r["is_external"] is True


def test_external_deposit_pekao() -> None:
    r = parse_comment("Deposit", "Pekao S.A. transfer")
    assert r["is_external"] is True


def test_internal_deposit() -> None:
    r = parse_comment("Deposit", "some internal move")
    assert r["is_external"] is False


def test_dividend_native_ccy() -> None:
    r = parse_comment("Dividend", "EUR 0.0960/ SHR")
    assert r["native_currency"] == "EUR"
    assert r["per_share"] == Decimal("0.0960")


def test_foreign_dividend_type() -> None:
    r = parse_comment("Dividend from foreign company on PL market", "USD 1.2500/ SHR")
    assert r["native_currency"] == "USD"


def test_subaccount_transfer() -> None:
    r = parse_comment("Subaccount transfer", "Transfer from 50320481 to 51109778")
    assert r["from_id"] == "50320481"
    assert r["to_id"] == "51109778"


def test_unknown_type_returns_empty() -> None:
    r = parse_comment("Some future type", "anything")
    assert r == {}
