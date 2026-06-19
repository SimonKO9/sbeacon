from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from portfolio_tracker.domain.accounts import Account, Wrapper
from portfolio_tracker.domain.events import Event, EventType, SourceRef
from portfolio_tracker.domain.instruments import AssetClass, Instrument
from portfolio_tracker.pricing.yahoo import search_yf_symbol

from .parse import load_base_currency, parse_csv

logger = logging.getLogger(__name__)

BROKER = "IBKR"

# Per-process cache: (bare_symbol, currency) → resolved Yahoo ticker.
_resolution_cache: dict[tuple[str, str | None], str] = {}

TYPE_MAP: dict[str, EventType] = {
    "buy": EventType.TRADE,
    "sell": EventType.TRADE,
    "deposit": EventType.DEPOSIT,
    "withdrawal": EventType.WITHDRAWAL,
    "forex trade component": EventType.FX_CONVERSION,
}

SKIP_TYPES = frozenset({"adjustment"})


def _resolve_symbol(symbol: str, currency: str | None) -> str:
    """Return Yahoo Finance ticker for a bare IBKR symbol, using search + currency filter.

    Symbols that already carry an exchange suffix (e.g. PRX.NL from XTB) are
    returned unchanged.  Results are cached in _resolution_cache.
    """
    if "." in symbol:
        return symbol
    key = (symbol, currency)
    if key not in _resolution_cache:
        resolved = search_yf_symbol(symbol, currency) or symbol
        _resolution_cache[key] = resolved
    return _resolution_cache[key]


def normalize(raw: dict[str, Any], account: Account, source_file: Path) -> Event | None:
    txn_type_lower = raw["txn_type"].lower().strip()

    if txn_type_lower in SKIP_TYPES:
        return None

    event_type = TYPE_MAP.get(txn_type_lower)
    if event_type is None:
        logger.warning(
            "Unrecognized Transaction Type %r in row %d — skipped",
            raw["txn_type"],
            raw["source_row"],
        )
        return None

    net_amount = raw.get("net_amount")
    if net_amount is None:
        logger.warning("Row %d has no net amount — skipped", raw["source_row"])
        return None

    date_str = raw.get("date_str", "")
    try:
        ts = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        logger.warning("Row %d: bad date %r — skipped", raw["source_row"], date_str)
        return None

    symbol = raw.get("symbol")
    price_currency = raw.get("price_currency")

    instrument: Instrument | None = None
    if symbol and event_type == EventType.TRADE:
        resolved_symbol = _resolve_symbol(symbol, price_currency)
        instrument = Instrument(
            symbol=resolved_symbol,
            name=raw.get("description") or None,
            asset_class=AssetClass.EQUITY,
            quote_currency=price_currency if price_currency and price_currency != "-" else None,
        )

    quantity: Decimal | None = raw.get("quantity")
    if event_type == EventType.TRADE and quantity is not None:
        if txn_type_lower == "sell" and quantity > 0:
            quantity = -quantity

    commission = raw.get("commission")
    fees = abs(commission) if commission is not None else Decimal(0)

    return Event(
        id=raw["id"],
        account_id=account.account_id,
        timestamp=ts,
        type=event_type,
        amount=net_amount,
        currency=account.base_currency,
        source=SourceRef(
            file=source_file,
            sheet="Transaction History",
            row=raw["source_row"],
            raw=raw.get("raw", {}),
        ),
        instrument=instrument,
        quantity=quantity if event_type == EventType.TRADE else None,
        price=raw.get("price") if event_type == EventType.TRADE else None,
        fees=fees,
    )


def load(paths: list[Path], dry_run: bool = False) -> list[Event]:
    """Discover, parse, and normalize IBKR export CSV files into Events."""
    events: list[Event] = []
    skipped = 0

    for path in paths:
        candidates = sorted(path.rglob("*.csv")) if path.is_dir() else [path]
        for csv_path in candidates:
            account_num = csv_path.stem.split(".")[0]
            account_id = f"{BROKER}_{account_num}"
            base_currency = load_base_currency(csv_path)
            account = Account(
                account_id=account_id,
                broker=BROKER,
                wrapper=Wrapper.REGULAR,
                base_currency=base_currency,
            )
            logger.info("Parsing %s → %s (%s)", csv_path.name, account_id, base_currency)
            for raw in parse_csv(csv_path, account_id):
                event = normalize(raw, account, csv_path)
                if event is not None:
                    events.append(event)
                else:
                    skipped += 1

    if dry_run:
        counts = Counter(e.type.value for e in events)
        logger.info(
            "Dry run: %d events (%d skipped) — %s",
            len(events),
            skipped,
            dict(counts),
        )

    return events
