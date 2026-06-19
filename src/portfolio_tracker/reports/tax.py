from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from portfolio_tracker.domain.events import Event, EventType
from portfolio_tracker.reports.lots import Lot, fifo

if TYPE_CHECKING:
    from portfolio_tracker.pricing.provider import Quote

TAX_RATE = Decimal("0.19")


def _is_regular(account_id: str) -> bool:
    parts = account_id.upper().split("_")
    return not any(p in ("IKE", "IKZE") for p in parts)


def _tax_fifo_match(events: list[Event]) -> tuple[list[Lot], list]:
    """FIFO per symbol across the REGULAR pool (not per-account).

    Returns (open_lots, closed_lots).  Lots from different REGULAR accounts
    for the same instrument are merged and matched in timestamp order.
    Currency mismatches across accounts (e.g. PLN buy vs USD buy of the same
    ticker) are a known limitation; in practice all buys/sells of one ticker
    reside in one account.
    """
    from portfolio_tracker.reports.lots import ClosedLot

    groups: defaultdict[str, list[Event]] = defaultdict(list)
    for e in events:
        if e.type == EventType.TRADE and e.instrument and _is_regular(e.account_id):
            groups[e.instrument.symbol].append(e)

    all_open: list[Lot] = []
    all_closed: list[ClosedLot] = []
    for symbol_events in groups.values():
        open_, closed = fifo(symbol_events)
        all_open.extend(open_)
        all_closed.extend(closed)
    return all_open, all_closed


# ── result types ──────────────────────────────────────────────────────────────

@dataclass
class TaxDisposal:
    symbol: str
    sell_date: date
    quantity: Decimal
    proceeds_pln: Decimal
    cost_pln: Decimal
    gain_pln: Decimal


@dataclass
class TaxSummaryResult:
    year: int
    disposals: list[TaxDisposal]
    proceeds_pln: Decimal
    cost_basis_pln: Decimal
    deductible_costs_pln: Decimal  # positive amount (fees absorbed as deduction)
    net_gain_pln: Decimal
    tax_pln: Decimal
    estimate: bool


@dataclass
class TaxLotRow:
    buy_date: date
    quantity: Decimal
    cost_per_unit_pln: Decimal
    total_cost_pln: Decimal
    current_price_pln: Decimal | None
    unrealized_pln: Decimal | None


@dataclass
class TaxLotsResult:
    symbol: str
    lots: list[TaxLotRow]
    total_cost_pln: Decimal
    total_unrealized_pln: Decimal | None
    estimate: bool


@dataclass
class HarvestCandidate:
    symbol: str
    quantity: Decimal
    fifo_loss_pln: Decimal      # negative
    tax_saved_pln: Decimal      # positive: 19% of abs(loss)
    cumulative_offset_pln: Decimal  # running total (negative = cumulative loss offset)


@dataclass
class HarvestResult:
    year: int
    ytd_gain_pln: Decimal
    ytd_tax_due_pln: Decimal
    candidates: list[HarvestCandidate]
    days_to_year_end: int
    estimate: bool


# ── compute functions ─────────────────────────────────────────────────────────

def compute_tax_summary(
    events: list[Event],
    fx_rates: dict[str, Decimal],
    year: int,
    estimate: bool = True,
    fx_fn: Callable[[str, date], Decimal] | None = None,
) -> TaxSummaryResult:
    """PIT-38 capital-gains roll-up for a given year (REGULAR pool).

    fx_fn: callable(currency, transaction_date) → PLN rate using NBP D-1.
           When provided, each lot uses the D-1 rate for its buy/sell date
           and estimate is set to False automatically.
    fx_rates: fallback live rates used when fx_fn is None (estimate mode).
    """
    if fx_fn is not None:
        estimate = False

    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    events_by_id = {e.id: e for e in events}

    def _live_rate(currency: str) -> Decimal:
        if currency == "PLN":
            return Decimal(1)
        return fx_rates.get(currency, Decimal(1))

    _, closed_lots = _tax_fifo_match(events)

    disposals: list[TaxDisposal] = []
    for cl in closed_lots:
        sell_ev = events_by_id.get(cl.sell_event_id)
        if sell_ev is None:
            continue
        sell_date = sell_ev.timestamp.date()
        if not (year_start <= sell_date <= year_end):
            continue

        ccy = cl.buy_lot.currency
        sell_cost_per_unit = cl.buy_lot.cost_per_unit + cl.realized_pnl / cl.quantity

        if fx_fn is not None and ccy != "PLN":
            buy_ev = events_by_id.get(cl.buy_lot.event_id)
            buy_date = buy_ev.timestamp.date() if buy_ev else sell_date
            cost_rate = fx_fn(ccy, buy_date)
            proceeds_rate = fx_fn(ccy, sell_date)
        else:
            cost_rate = proceeds_rate = _live_rate(ccy)

        cost_pln = cl.buy_lot.cost_per_unit * cl.quantity * cost_rate
        proceeds_pln = sell_cost_per_unit * cl.quantity * proceeds_rate

        disposals.append(TaxDisposal(
            symbol=cl.buy_lot.symbol,
            sell_date=sell_date,
            quantity=cl.quantity,
            proceeds_pln=proceeds_pln,
            cost_pln=cost_pln,
            gain_pln=proceeds_pln - cost_pln,
        ))

    disposals.sort(key=lambda d: d.sell_date)

    proceeds_total = sum((d.proceeds_pln for d in disposals), Decimal(0))
    cost_total = sum((d.cost_pln for d in disposals), Decimal(0))

    # Deductible costs: FEE events in REGULAR accounts within the year
    deductible_pln = Decimal(0)
    for e in events:
        if e.type != EventType.FEE or not _is_regular(e.account_id):
            continue
        ev_date = e.timestamp.date()
        if not (year_start <= ev_date <= year_end):
            continue
        fee_rate = fx_fn(e.currency, ev_date) if fx_fn and e.currency != "PLN" else _live_rate(e.currency)
        deductible_pln += abs(e.amount * fee_rate)

    net_gain = proceeds_total - cost_total - deductible_pln
    tax = net_gain * TAX_RATE if net_gain > 0 else Decimal(0)

    return TaxSummaryResult(
        year=year,
        disposals=disposals,
        proceeds_pln=proceeds_total,
        cost_basis_pln=cost_total,
        deductible_costs_pln=deductible_pln,
        net_gain_pln=net_gain,
        tax_pln=tax,
        estimate=estimate,
    )


def compute_tax_lots(
    events: list[Event],
    symbol: str,
    prices: dict[str, "Quote"],
    fx_rates: dict[str, Decimal],
    estimate: bool = True,
    fx_fn: Callable[[str, date], Decimal] | None = None,
) -> TaxLotsResult:
    """FIFO lot / tax-basis inspection for one instrument in the REGULAR pool."""
    if fx_fn is not None:
        estimate = False

    events_by_id = {e.id: e for e in events}

    def _live_rate(currency: str) -> Decimal:
        if currency == "PLN":
            return Decimal(1)
        return fx_rates.get(currency, Decimal(1))

    open_lots, _ = _tax_fifo_match(events)
    instrument_lots = [
        lot for lot in open_lots
        if lot.symbol == symbol and lot.remaining > 0
    ]
    instrument_lots.sort(
        key=lambda lot: events_by_id[lot.event_id].timestamp.date()
        if lot.event_id in events_by_id else date.min
    )

    current_price_pln: Decimal | None = None
    if symbol in prices:
        q = prices[symbol]
        # unrealized value uses live rate — this is for planning, not filing
        current_price_pln = q.price * _live_rate(q.currency)

    lot_rows: list[TaxLotRow] = []
    total_cost = Decimal(0)
    total_unrealized: Decimal | None = Decimal(0) if current_price_pln is not None else None

    for lot in instrument_lots:
        buy_ev = events_by_id.get(lot.event_id)
        buy_date = buy_ev.timestamp.date() if buy_ev else date.min

        if fx_fn is not None and lot.currency != "PLN":
            cost_rate = fx_fn(lot.currency, buy_date)
        else:
            cost_rate = _live_rate(lot.currency)

        cost_per_unit_pln = lot.cost_per_unit * cost_rate
        total_cost_lot = cost_per_unit_pln * lot.remaining
        total_cost += total_cost_lot

        unrealized = None
        if current_price_pln is not None:
            unrealized = (current_price_pln - cost_per_unit_pln) * lot.remaining
            total_unrealized += unrealized  # type: ignore[operator]

        lot_rows.append(TaxLotRow(
            buy_date=buy_date,
            quantity=lot.remaining,
            cost_per_unit_pln=cost_per_unit_pln,
            total_cost_pln=total_cost_lot,
            current_price_pln=current_price_pln,
            unrealized_pln=unrealized,
        ))

    return TaxLotsResult(
        symbol=symbol,
        lots=lot_rows,
        total_cost_pln=total_cost,
        total_unrealized_pln=total_unrealized,
        estimate=estimate,
    )


def compute_tax_harvest(
    events: list[Event],
    prices: dict[str, "Quote"],
    fx_rates: dict[str, Decimal],
    year: int,
    estimate: bool = True,
    fx_fn: Callable[[str, date], Decimal] | None = None,
) -> HarvestResult:
    """Loss-harvesting candidates for the REGULAR pool.

    Finds all open positions with a net FIFO loss at current prices (tax basis),
    ranks them, and shows cumulative offset against YTD realized gains.
    """
    today = date.today()
    year_end = date(year, 12, 31)
    days_to_year_end = max(0, (year_end - today).days)

    ytd = compute_tax_summary(events, fx_rates=fx_rates, year=year, estimate=estimate, fx_fn=fx_fn)

    def _to_pln(amount: Decimal, currency: str) -> Decimal:
        if currency == "PLN":
            return amount
        return amount * fx_rates.get(currency, Decimal(1))

    open_lots, _ = _tax_fifo_match(events)

    lots_by_symbol: defaultdict[str, list[Lot]] = defaultdict(list)
    for lot in open_lots:
        if lot.remaining > 0:
            lots_by_symbol[lot.symbol].append(lot)

    candidates: list[HarvestCandidate] = []
    for symbol, lots in lots_by_symbol.items():
        if symbol not in prices:
            continue
        q = prices[symbol]
        price_pln = q.price * _to_pln(Decimal(1), q.currency)

        total_loss = sum(
            (price_pln - _to_pln(lot.cost_per_unit, lot.currency)) * lot.remaining
            for lot in lots
        )
        if total_loss >= 0:
            continue

        total_qty = sum(lot.remaining for lot in lots)
        candidates.append(HarvestCandidate(
            symbol=symbol,
            quantity=total_qty,
            fifo_loss_pln=total_loss,
            tax_saved_pln=abs(total_loss) * TAX_RATE,
            cumulative_offset_pln=Decimal(0),  # filled below
        ))

    candidates.sort(key=lambda c: c.fifo_loss_pln)

    running = Decimal(0)
    for c in candidates:
        running += c.fifo_loss_pln
        c.cumulative_offset_pln = running

    return HarvestResult(
        year=year,
        ytd_gain_pln=ytd.net_gain_pln,
        ytd_tax_due_pln=ytd.tax_pln,
        candidates=candidates,
        days_to_year_end=days_to_year_end,
        estimate=ytd.estimate,
    )
