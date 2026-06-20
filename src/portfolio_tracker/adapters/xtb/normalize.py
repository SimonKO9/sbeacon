from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from portfolio_tracker.domain.accounts import Account
from portfolio_tracker.domain.events import Event, EventType, SourceRef
from portfolio_tracker.domain.instruments import AssetClass, Instrument

from .discover import discover
from .parse import parse_workbook

logger = logging.getLogger(__name__)

TYPE_MAP: dict[str, EventType] = {
    "stock purchase": EventType.TRADE,
    "stock sell": EventType.TRADE,
    "dividend": EventType.DIVIDEND,
    "dividend from foreign company on pl market": EventType.DIVIDEND,
    "free funds interest": EventType.INTEREST,
    "free funds interest tax": EventType.TAX,
    "withholding tax": EventType.TAX,
    "sec fee": EventType.FEE,
    "deposit": EventType.DEPOSIT,
    "transfer": EventType.FX_CONVERSION,
    "subaccount transfer": EventType.TRANSFER,
    "ike deposit": EventType.TRANSFER,
    "ikze deposit": EventType.TRANSFER,
    "withdrawal": EventType.WITHDRAWAL,
}


def normalize(raw: dict[str, Any], account: Account, source_file: Path) -> Event | None:
    type_str = raw["type_str"].lower().strip()
    event_type = TYPE_MAP.get(type_str)
    if event_type is None:
        logger.warning("Unrecognized Type %r in row %d — skipped", raw["type_str"], raw["source_row"])  # noqa: E501
        return None

    amount = raw.get("amount")
    if amount is None:
        logger.warning("Row %d has no amount — skipped", raw["source_row"])
        return None

    timestamp = raw["timestamp"]
    if not isinstance(timestamp, datetime):
        logger.warning(  # noqa: E501
            "Row %d: unexpected timestamp type %s — skipped", raw["source_row"], type(timestamp)
        )
        return None

    parsed = raw.get("comment_parsed", {})
    symbol = raw.get("symbol")
    instrument: Instrument | None = (
        Instrument(symbol=symbol, name=raw.get("instrument_name"), asset_class=AssetClass.EQUITY)
        if symbol
        else None
    )

    quantity = parsed.get("quantity")
    if quantity is not None and event_type == EventType.TRADE:
        # Sign: OPEN action = buy (positive), CLOSE = sell (negative)
        action = parsed.get("action", "").upper()
        if action == "CLOSE":
            quantity = -quantity

    return Event(
        id=raw["id"],
        account_id=account.account_id,
        timestamp=timestamp,
        type=event_type,
        amount=amount,
        currency=account.base_currency,
        source=SourceRef(
            file=source_file,
            sheet="Cash operations",
            row=raw["source_row"],
            raw=raw.get("raw", {}),
        ),
        instrument=instrument,
        quantity=quantity,
        price=parsed.get("price"),
    )


def load(paths: list[Path], dry_run: bool = False) -> list[Event]:
    """Discover, parse, and normalize XTB export files into Events."""
    events: list[Event] = []
    skipped = 0

    for path, account in discover(paths):
        logger.info("Parsing %s → %s", path.name, account.account_id)
        for raw in parse_workbook(path, account):
            event = normalize(raw, account, path)
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

    return events
