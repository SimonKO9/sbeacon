from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from portfolio_tracker.domain.events import Event, EventType
from portfolio_tracker.reports.lots import FIFOPolicy
from portfolio_tracker.reports.positions import compute_positions

if TYPE_CHECKING:
    from portfolio_tracker.pricing.provider import Quote


def _wrapper_of(account_id: str) -> str:
    uid = account_id.upper()
    if uid.startswith("IKZE"):
        return "IKZE"
    if uid.startswith("IKE"):
        return "IKE"
    return "REGULAR"


@dataclass
class PnlRow:
    group_key: str
    realized_pln: Decimal = Decimal(0)
    unrealized_pln: Decimal = Decimal(0)
    dividends_pln: Decimal = Decimal(0)
    interest_pln: Decimal = Decimal(0)
    fees_pln: Decimal = Decimal(0)
    taxes_pln: Decimal = Decimal(0)
    cost_basis_closed_pln: Decimal = Decimal(0)
    cost_basis_open_pln: Decimal = Decimal(0)

    @property
    def income_pln(self) -> Decimal:
        return self.dividends_pln + self.interest_pln

    @property
    def total_pln(self) -> Decimal:
        return self.realized_pln + self.unrealized_pln + self.income_pln + self.fees_pln + self.taxes_pln

    @property
    def cost_basis_pln(self) -> Decimal:
        return self.cost_basis_closed_pln + self.cost_basis_open_pln

    @property
    def realized_return_pct(self) -> Decimal | None:
        if self.cost_basis_closed_pln <= 0:
            return None
        return self.realized_pln / self.cost_basis_closed_pln * Decimal(100)

    @property
    def unrealized_return_pct(self) -> Decimal | None:
        if self.cost_basis_open_pln <= 0:
            return None
        return self.unrealized_pln / self.cost_basis_open_pln * Decimal(100)

    @property
    def total_return_pct(self) -> Decimal | None:
        if self.cost_basis_pln <= 0:
            return None
        return self.total_pln / self.cost_basis_pln * Decimal(100)


@dataclass
class PnlResult:
    rows: list[PnlRow]
    fx_cash_pln: Decimal
    total: PnlRow
    as_of: date
    period_label: str
    by: str
    unpriced_symbols: list[str]


def compute_pnl(
    events: list[Event],
    prices: dict[str, "Quote"],
    fx_rates: dict[str, Decimal],
    by: str = "instrument",
    date_from: date | None = None,
    date_to: date | None = None,
    sort_by: str = "total",
) -> PnlResult:
    """Compute P/L grouped by dimension, period-bounded for realized/income.

    Unrealized is always the current snapshot regardless of --period.
    fx_cash_pln is the residual that ties total to summary's global P/L (lifetime only).
    """
    as_of = date.today()

    if date_from and date_to and date_from.month == 1 and date_to.month == 12 and date_from.year == date_to.year:
        period_label = str(date_from.year)
    elif date_from and date_to:
        period_label = f"{date_from} – {date_to}"
    elif date_from:
        period_label = f"from {date_from}"
    elif date_to:
        period_label = f"to {date_to}"
    else:
        period_label = "lifetime"

    def _rate(currency: str) -> Decimal:
        if currency == "PLN":
            return Decimal(1)
        return fx_rates.get(currency, Decimal(1))

    def _in_period(d: date) -> bool:
        if date_from and d < date_from:
            return False
        if date_to and d > date_to:
            return False
        return True

    events_by_id: dict[str, Event] = {e.id: e for e in events}

    symbol_to_asset_class: dict[str, str] = {}
    for e in events:
        if e.instrument and e.instrument.asset_class:
            symbol_to_asset_class[e.instrument.symbol] = e.instrument.asset_class.value

    def _group_key(*, symbol: str | None, account_id: str, currency: str) -> str:
        if by == "instrument":
            return symbol or "Portfolio"
        if by == "account":
            return account_id
        if by == "currency":
            return currency
        if by == "wrapper":
            return _wrapper_of(account_id)
        if by == "asset-class":
            return symbol_to_asset_class.get(symbol or "", "Unknown") if symbol else "Unknown"
        return "unknown"

    rows: dict[str, PnlRow] = {}

    def _row(key: str) -> PnlRow:
        if key not in rows:
            rows[key] = PnlRow(group_key=key)
        return rows[key]

    # ── realized P/L ──────────────────────────────────────────────────────
    closed_lots = FIFOPolicy().match(events)
    for cl in closed_lots:
        sell_ev = events_by_id.get(cl.sell_event_id)
        if sell_ev and not _in_period(sell_ev.timestamp.date()):
            continue
        rate = _rate(cl.buy_lot.currency)
        key = _group_key(
            symbol=cl.buy_lot.symbol,
            account_id=cl.buy_lot.account_id,
            currency=cl.buy_lot.currency,
        )
        r = _row(key)
        r.realized_pln += cl.realized_pnl * rate
        r.cost_basis_closed_pln += cl.buy_lot.cost_per_unit * cl.quantity * rate

    # ── income / expense ──────────────────────────────────────────────────
    for e in events:
        if e.type not in {EventType.DIVIDEND, EventType.INTEREST, EventType.FEE, EventType.TAX}:
            continue
        if not _in_period(e.timestamp.date()):
            continue
        amount_pln = e.amount * _rate(e.currency)
        sym = e.instrument.symbol if e.instrument else None
        key = _group_key(symbol=sym, account_id=e.account_id, currency=e.currency)
        r = _row(key)
        if e.type == EventType.DIVIDEND:
            r.dividends_pln += amount_pln
        elif e.type == EventType.INTEREST:
            r.interest_pln += amount_pln
        elif e.type == EventType.FEE:
            r.fees_pln += amount_pln
        elif e.type == EventType.TAX:
            r.taxes_pln += amount_pln

    # ── open positions → unrealized (always current snapshot) ─────────────
    unpriced: list[str] = []
    trade_events = [e for e in events if e.type == EventType.TRADE]
    positions = compute_positions(trade_events, prices=prices, fx_rates=fx_rates)

    for pos in positions:
        key = _group_key(symbol=pos.symbol, account_id=pos.account_id, currency=pos.cost_currency)
        r = _row(key)
        r.cost_basis_open_pln += pos.avg_cost * pos.quantity * _rate(pos.cost_currency)
        if pos.unrealized_pnl_pln is not None:
            r.unrealized_pln += pos.unrealized_pnl_pln
        else:
            unpriced.append(pos.symbol)

    # ── sort ──────────────────────────────────────────────────────────────
    _sort_key = {
        "realized": lambda r: r.realized_pln,
        "unrealized": lambda r: r.unrealized_pln,
        "total": lambda r: r.total_pln,
    }.get(sort_by, lambda r: r.total_pln)
    row_list = sorted(rows.values(), key=_sort_key, reverse=True)

    # ── total row ─────────────────────────────────────────────────────────
    total = PnlRow(group_key="TOTAL")
    for r in row_list:
        total.realized_pln += r.realized_pln
        total.unrealized_pln += r.unrealized_pln
        total.dividends_pln += r.dividends_pln
        total.interest_pln += r.interest_pln
        total.fees_pln += r.fees_pln
        total.taxes_pln += r.taxes_pln
        total.cost_basis_closed_pln += r.cost_basis_closed_pln
        total.cost_basis_open_pln += r.cost_basis_open_pln

    # ── fx/cash residual (lifetime only — ties to summary) ────────────────
    # global_pnl = market_value + cash − net_in; residual = global_pnl − Σ components
    fx_cash_pln = Decimal(0)
    if period_label == "lifetime":
        account_base: dict[str, str] = {}
        for e in events:
            account_base[e.account_id] = e.currency

        net_in_global = sum(
            (e.amount * _rate(account_base.get(e.account_id, "PLN"))
             for e in events
             if e.type in {EventType.DEPOSIT, EventType.WITHDRAWAL}),
            Decimal(0),
        )
        cash_pln_total = sum(
            (e.amount * _rate(account_base.get(e.account_id, "PLN")) for e in events),
            Decimal(0),
        )
        market_value_pln_total = sum(
            (pos.market_value_pln or Decimal(0) for pos in positions),
            Decimal(0),
        )
        global_pnl = market_value_pln_total + cash_pln_total - net_in_global
        fx_cash_pln = global_pnl - total.total_pln

    return PnlResult(
        rows=row_list,
        fx_cash_pln=fx_cash_pln,
        total=total,
        as_of=as_of,
        period_label=period_label,
        by=by,
        unpriced_symbols=list(dict.fromkeys(unpriced)),
    )
