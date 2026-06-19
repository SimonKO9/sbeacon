from __future__ import annotations

import csv
import hashlib
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _parse_decimal(s: str) -> Decimal | None:
    s = s.strip()
    if not s or s == "-":
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        logger.warning("Could not parse decimal: %r", s)
        return None


def _row_id(
    account_id: str,
    date_str: str,
    txn_type: str,
    symbol: str,
    net_amount: str,
    row_idx: int,
) -> str:
    content = f"{account_id}:{date_str}:{txn_type}:{symbol}:{net_amount}:{row_idx}"
    return hashlib.sha1(content.encode()).hexdigest()[:16]


def load_base_currency(path: Path) -> str:
    """Extract base currency from IBKR CSV Summary section."""
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if (
                len(row) >= 4
                and row[0].strip() == "Summary"
                and row[1].strip() == "Data"
                and row[2].strip() == "Base Currency"
            ):
                return row[3].strip()
    return "PLN"


def parse_csv(path: Path, account_id: str) -> list[dict[str, Any]]:
    """Parse IBKR Transaction History CSV into raw row dicts."""
    col_names: list[str] = []
    rows: list[dict[str, Any]] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row_idx, row in enumerate(reader, start=1):
            if not row or len(row) < 2:
                continue
            section = row[0].strip()
            row_type = row[1].strip()

            if section == "Transaction History" and row_type == "Header":
                col_names = [c.strip() for c in row[2:]]
            elif section == "Transaction History" and row_type == "Data" and col_names:
                data = row[2:]
                record: dict[str, str] = {
                    col_names[i]: (data[i].strip() if i < len(data) else "")
                    for i in range(len(col_names))
                }

                date_str = record.get("Date", "")
                txn_type = record.get("Transaction Type", "")
                symbol_raw = record.get("Symbol", "")
                net_amount_str = record.get("Net Amount", "")

                symbol = symbol_raw if symbol_raw and symbol_raw != "-" else None

                row_id = _row_id(account_id, date_str, txn_type, symbol_raw, net_amount_str, row_idx)

                rows.append({
                    "id": f"{account_id}:{row_id}",
                    "source_row": row_idx,
                    "raw": record,
                    "date_str": date_str,
                    "description": record.get("Description", ""),
                    "txn_type": txn_type,
                    "symbol": symbol,
                    "quantity": _parse_decimal(record.get("Quantity", "")),
                    "price": _parse_decimal(record.get("Price", "")),
                    "price_currency": record.get("Price Currency", "").strip() or None,
                    "gross_amount": _parse_decimal(record.get("Gross Amount", "")),
                    "commission": _parse_decimal(record.get("Commission", "")),
                    "net_amount": _parse_decimal(net_amount_str),
                    "exchange_rate": _parse_decimal(record.get("Exchange Rate", "")),
                })

    return rows
