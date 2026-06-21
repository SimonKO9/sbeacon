from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from portfolio_tracker.domain.events import Event, EventType
from portfolio_tracker.reports.lots import fifo

if TYPE_CHECKING:
    from portfolio_tracker.pricing.provider import Quote

# Currency of the price embedded in the XTB export comment (per suffix heuristic).
# .UK can be USD for some instruments — needs ISIN master (DESIGN.md §8).
_SUFFIX_TO_CURRENCY: dict[str, str] = {
    "US": "USD",
    "PL": "PLN",
    "DE": "EUR",
    "NL": "EUR",
    "UK": "GBP",
}


def _cost_currency(symbol: str) -> str:
    """Currency of avg_cost: derived from XTB symbol suffix (the exchange price currency)."""
    suffix = symbol.rsplit(".", 1)[-1].upper() if "." in symbol else ""
    return _SUFFIX_TO_CURRENCY.get(suffix, "USD")


@dataclass
class Position:
    symbol: str
    account_id: str
    quantity: Decimal
    avg_cost: Decimal       # per-share cost, in cost_currency
    cost_currency: str      # currency of avg_cost (from XTB exchange, fixed)
    quote_currency: str     # currency of current_price (from price provider, may differ from cost_currency)
    current_price: Decimal | None = None
    market_value: Decimal | None = None     # qty * current_price, in quote_currency
    unrealized_pnl: Decimal | None = None  # (current_price - avg_cost) * qty — None if currencies differ
    market_value_pln: Decimal | None = None
    unrealized_pnl_pln: Decimal | None = None
    weight_pct: Decimal | None = None


def compute_positions(
    events: list[Event],
    prices: dict[str, "Quote"] | None = None,
    fx_rates: dict[str, Decimal] | None = None,
) -> list[Position]:
    """Derive open positions from TRADE events using FIFO lot matching.

    prices:   symbol → Quote; fills current_price, market_value, unrealized_pnl.
    fx_rates: currency → PLN rate; fills *_pln fields and weight_pct.

    unrealized_pnl (native) is only set when cost_currency == quote_currency.
    unrealized_pnl_pln is always computed via PLN values and is always correct.

    TODO: use wrapper-pool scope for REGULAR accounts (§7) — currently per-account.
    """
    groups: defaultdict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in events:
        if event.type == EventType.TRADE and event.instrument:
            groups[(event.account_id, event.instrument.symbol)].append(event)

    positions: list[Position] = []
    for (account_id, symbol), group_events in sorted(groups.items()):
        open_lots, _ = fifo(group_events)
        if not open_lots:
            continue

        total_qty = sum((lot.remaining for lot in open_lots), Decimal(0))
        if total_qty == 0:
            continue

        total_cost = sum((lot.remaining * lot.cost_per_unit for lot in open_lots), Decimal(0))
        cost_ccy = group_events[0].currency  # account base currency (already in event.amount)

        pos = Position(
            symbol=symbol,
            account_id=account_id,
            quantity=total_qty,
            avg_cost=total_cost / total_qty,
            cost_currency=cost_ccy,
            quote_currency=cost_ccy,  # overridden below when prices are available
        )

        if prices and symbol in prices:
            q = prices[symbol]
            pos.quote_currency = q.currency
            pos.current_price = q.price
            pos.market_value = total_qty * q.price
            if pos.cost_currency == pos.quote_currency:
                pos.unrealized_pnl = (q.price - pos.avg_cost) * total_qty

        positions.append(pos)

    if prices:
        _fx = fx_rates or {}
        for pos in positions:
            if pos.market_value is None:
                continue
            price_rate = _fx.get(pos.quote_currency, Decimal("1")) if pos.quote_currency != "PLN" else Decimal("1")
            cost_rate = _fx.get(pos.cost_currency, Decimal("1")) if pos.cost_currency != "PLN" else Decimal("1")
            pos.market_value_pln = pos.market_value * price_rate
            cost_value_pln = pos.avg_cost * pos.quantity * cost_rate
            pos.unrealized_pnl_pln = pos.market_value_pln - cost_value_pln

            # Cost is PLN but price comes from a foreign proxy ticker (e.g. LYPS.DE for LYPS.PL).
            # Convert native fields to PLN so the non-PLN columns are meaningful.
            if pos.cost_currency == "PLN" and pos.quote_currency != "PLN":
                if pos.current_price is not None:
                    pos.current_price = pos.current_price * price_rate
                pos.market_value = pos.market_value_pln
                pos.unrealized_pnl = pos.unrealized_pnl_pln
                pos.quote_currency = "PLN"

        total_pln = sum((p.market_value_pln for p in positions if p.market_value_pln is not None), Decimal(0))
        if total_pln > 0:
            for pos in positions:
                if pos.market_value_pln is not None:
                    pos.weight_pct = pos.market_value_pln / total_pln * Decimal(100)

    positions.sort(key=lambda p: p.market_value_pln or Decimal(0), reverse=True)
    return positions
