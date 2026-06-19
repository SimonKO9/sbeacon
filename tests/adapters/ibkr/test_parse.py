from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_tracker.adapters.ibkr.parse import load_base_currency, parse_csv

SAMPLE_CSV = """\
Statement,Header,Field Name,Field Value
Statement,Data,Title,Transaction History
Summary,Header,Field Name,Field Value
Summary,Data,Base Currency,PLN
Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount,Commission,Net Amount,Sub Type,Exchange Rate,Transaction Fees,Multiplier
Transaction History,Data,2026-05-28,U***19041,PROSUS NV,Buy,PRX,108.0,38.6,EUR,-17623.602,-12.6825,-17636.2845,-,4.2275,-,1.0
Transaction History,Data,2026-05-15,U***19041,Electronic Fund Transfer,Deposit,-,-,-,-,35000.0,-,35000.0,-,1.0,-,1.0
Transaction History,Data,2026-06-15,U***19041,FX Translations P&L,Adjustment,-,-,-,-,-33.213,-,-33.213,-,1.0,-,1.0
Transaction History,Data,2026-04-21,U***19041,"Net Amount in Base from Forex Trade: 7,085 EUR.PLN",Forex Trade Component,EUR.PLN,7085.0,4.23245,PLN,41.354,-7.1778,41.354,-,1.0,-,1.0
"""


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    p = tmp_path / "U12345678.TRANSACTIONS.1Y.csv"
    p.write_text(SAMPLE_CSV)
    return p


def test_load_base_currency(csv_file: Path) -> None:
    assert load_base_currency(csv_file) == "PLN"


def test_parse_csv_row_count(csv_file: Path) -> None:
    rows = parse_csv(csv_file, "IBKR_U12345678")
    assert len(rows) == 4


def test_parse_buy_row(csv_file: Path) -> None:
    rows = parse_csv(csv_file, "IBKR_U12345678")
    buy = rows[0]
    assert buy["txn_type"] == "Buy"
    assert buy["symbol"] == "PRX"
    assert buy["quantity"] == Decimal("108.0")
    assert buy["price"] == Decimal("38.6")
    assert buy["price_currency"] == "EUR"
    assert buy["net_amount"] == Decimal("-17636.2845")
    assert buy["commission"] == Decimal("-12.6825")
    assert buy["date_str"] == "2026-05-28"
    assert buy["id"].startswith("IBKR_U12345678:")


def test_parse_deposit_row(csv_file: Path) -> None:
    rows = parse_csv(csv_file, "IBKR_U12345678")
    dep = rows[1]
    assert dep["txn_type"] == "Deposit"
    assert dep["symbol"] is None
    assert dep["net_amount"] == Decimal("35000.0")


def test_parse_adjustment_row(csv_file: Path) -> None:
    rows = parse_csv(csv_file, "IBKR_U12345678")
    adj = rows[2]
    assert adj["txn_type"] == "Adjustment"


def test_parse_forex_row(csv_file: Path) -> None:
    rows = parse_csv(csv_file, "IBKR_U12345678")
    fx = rows[3]
    assert fx["txn_type"] == "Forex Trade Component"
    assert fx["symbol"] == "EUR.PLN"
    assert fx["quantity"] == Decimal("7085.0")
    assert fx["net_amount"] == Decimal("41.354")


def test_ids_are_stable(csv_file: Path) -> None:
    rows1 = parse_csv(csv_file, "IBKR_U12345678")
    rows2 = parse_csv(csv_file, "IBKR_U12345678")
    assert [r["id"] for r in rows1] == [r["id"] for r in rows2]


def test_ids_are_unique(csv_file: Path) -> None:
    rows = parse_csv(csv_file, "IBKR_U12345678")
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))
