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

@dataclass
class AccountRow:
    account_id: str
    base_currency: str
    # native amounts (in base_currency)
    market_value_native: Decimal
    cash_native: Decimal
    total_native: Decimal
    net_in_native: Decimal
    pnl_native: Decimal
    # PLN amounts
    market_value_pln: Decimal
    cash_pln: Decimal
    total_value_pln: Decimal
    net_in_pln: Decimal
    pnl_pln: Decimal
    pnl_pct: Decimal | None


@dataclass
class Decomposition:
    unrealized_pln: Decimal
    realized_pln: Decimal
    dividends_pln: Decimal
    interest_pln: Decimal
    fees_pln: Decimal      # negative values (cash out)
    taxes_pln: Decimal     # negative values (cash out)
    fx_on_cash_pln: Decimal  # residual: global P/L − Σ other components


@dataclass
class SummaryResult:
    account_rows: list[AccountRow]
    total: AccountRow
    decomposition: Decomposition
    xirr: float | None
    as_of: date
    unpriced_symbols: list[str]  # positions with no current price (stale/missing)


def _is_external_deposit(event: Event) -> bool:
    return event.type == EventType.DEPOSIT


def _xirr(cashflows: list[tuple[date, float]]) -> float | None:
    """Annualized money-weighted return via bisection on NPV = 0.

    Convention: outflows (deposits) are negative, final portfolio value is positive.
    """
    if len(cashflows) < 2:
        return None
    t0 = min(d for d, _ in cashflows)
    years = [(d - t0).days / 365.25 for d, _ in cashflows]
    amounts = [a for _, a in cashflows]

    def npv(r: float) -> float:
        return sum(a / (1.0 + r) ** t for a, t in zip(amounts, years))

    try:
        lo, hi = -0.9999, 100.0
        npv_lo = npv(lo)
        if npv_lo * npv(hi) > 0:
            return None
        for _ in range(200):
            mid = (lo + hi) / 2.0
            npv_mid = npv(mid)
            if abs(npv_mid) < 0.01 or (hi - lo) < 1e-7:
                return mid
            if npv_lo * npv_mid < 0:
                hi = mid
            else:
                lo = mid
                npv_lo = npv_mid
        return (lo + hi) / 2.0
    except Exception:
        return None


def compute_summary(
    events: list[Event],
    prices: dict[str, "Quote"],
    fx_rates: dict[str, Decimal],
) -> SummaryResult:
    """Compute portfolio summary.

    prices:   symbol → Quote (current price in native currency)
    fx_rates: currency → PLN rate e.g. {"USD": Decimal("3.67"), "EUR": Decimal("4.25")}

    Realized P/L is converted to PLN at current FX rates; any timing difference between
    historical and current FX is absorbed into decomposition.fx_on_cash_pln.

    TODO: --by=wrapper grouping (collapse PLN/EUR/USD into REGULAR pool).
    """
    as_of = date.today()

    # Account base currencies: derive from any event per account
    account_base: dict[str, str] = {}
    for e in events:
        account_base[e.account_id] = e.currency

    def to_pln(amount: Decimal, currency: str) -> Decimal:
        if currency == "PLN":
            return amount
        return amount * fx_rates.get(currency, Decimal("1"))

    # ── open positions ──────────────────────────────────────────────────────
    positions = compute_positions(
        [e for e in events if e.type == EventType.TRADE],
        prices=prices,
        fx_rates=fx_rates,
    )

    market_value_pln_by_account: dict[str, Decimal] = defaultdict(Decimal)
    unrealized_pln_total = Decimal(0)
    unpriced: list[str] = []

    for pos in positions:
        if pos.market_value_pln is not None:
            market_value_pln_by_account[pos.account_id] += pos.market_value_pln
        else:
            unpriced.append(pos.symbol)
        if pos.unrealized_pnl_pln is not None:
            unrealized_pln_total += pos.unrealized_pnl_pln

    # ── realized P/L ────────────────────────────────────────────────────────
    closed_lots = FIFOPolicy().match(events)
    realized_pln_by_account: dict[str, Decimal] = defaultdict(Decimal)
    for cl in closed_lots:
        ccy = cl.buy_lot.currency  # account base currency; realized_pnl is already in this currency
        rate = fx_rates.get(ccy, Decimal("1")) if ccy != "PLN" else Decimal("1")
        realized_pln_by_account[cl.buy_lot.account_id] += cl.realized_pnl * rate

    realized_pln_total = sum(realized_pln_by_account.values(), Decimal(0))

    # ── cash: Σ all event amounts per account (buys/sells already signed) ──
    cash_native_by_account: dict[str, Decimal] = defaultdict(Decimal)
    for e in events:
        cash_native_by_account[e.account_id] += e.amount

    cash_pln_by_account: dict[str, Decimal] = {
        acc: to_pln(native, account_base.get(acc, "PLN"))
        for acc, native in cash_native_by_account.items()
    }

    # ── income / expense ────────────────────────────────────────────────────
    dividends_pln = Decimal(0)
    interest_pln = Decimal(0)
    fees_pln = Decimal(0)
    taxes_pln = Decimal(0)

    for e in events:
        ccy = account_base.get(e.account_id, "PLN")
        a = to_pln(e.amount, ccy)
        if e.type == EventType.DIVIDEND:
            dividends_pln += a
        elif e.type == EventType.INTEREST:
            interest_pln += a
        elif e.type == EventType.FEE:
            fees_pln += a
        elif e.type == EventType.TAX:
            taxes_pln += a

    # ── net in ──────────────────────────────────────────────────────────────
    # Per-account: all inbound flows (deposits + inbound transfers)
    net_in_account: dict[str, Decimal] = defaultdict(Decimal)         # PLN
    net_in_native_account: dict[str, Decimal] = defaultdict(Decimal)  # native
    # Global: external bank deposits only
    net_in_global = Decimal(0)
    xirr_cashflows: list[tuple[date, float]] = []

    for e in events:
        ccy = account_base.get(e.account_id, "PLN")
        amount_pln = to_pln(e.amount, ccy)

        # Net capital allocated to this account: signed inbound + outbound flows.
        # Outbound TRANSFER/FX_CONVERSION (negative amounts) reduce per-account net_in,
        # so pass-through accounts (PLN hub) show ~0 P/L instead of -100%.
        if e.type in {EventType.DEPOSIT, EventType.TRANSFER, EventType.FX_CONVERSION, EventType.WITHDRAWAL}:
            net_in_account[e.account_id] += amount_pln
            net_in_native_account[e.account_id] += e.amount

        if _is_external_deposit(e):
            net_in_global += amount_pln
            xirr_cashflows.append((e.timestamp.date(), -float(amount_pln)))
        elif e.type == EventType.WITHDRAWAL:
            net_in_global += amount_pln
            xirr_cashflows.append((e.timestamp.date(), -float(amount_pln)))

    def _to_native(amount_pln: Decimal, base_ccy: str) -> Decimal:
        if base_ccy == "PLN":
            return amount_pln
        rate = fx_rates.get(base_ccy, Decimal("1"))
        return (amount_pln / rate) if rate else amount_pln

    # ── account rows ────────────────────────────────────────────────────────
    all_accounts = sorted(set(account_base))
    account_rows: list[AccountRow] = []

    for acc in all_accounts:
        base_ccy = account_base.get(acc, "PLN")
        mv_pln = market_value_pln_by_account.get(acc, Decimal(0))
        cash_native = cash_native_by_account.get(acc, Decimal(0))
        cash_pln = cash_pln_by_account.get(acc, Decimal(0))
        net_in_pln = net_in_account.get(acc, Decimal(0))
        net_in_native = net_in_native_account.get(acc, Decimal(0))

        mv_native = _to_native(mv_pln, base_ccy)
        total_native = mv_native + cash_native
        total_pln = mv_pln + cash_pln
        pnl_native = total_native - net_in_native
        pnl_pln = total_pln - net_in_pln
        pnl_pct = (pnl_pln / net_in_pln * Decimal(100)) if net_in_pln > 0 else None

        account_rows.append(AccountRow(
            account_id=acc,
            base_currency=base_ccy,
            market_value_native=mv_native,
            cash_native=cash_native,
            total_native=total_native,
            net_in_native=net_in_native,
            pnl_native=pnl_native,
            market_value_pln=mv_pln,
            cash_pln=cash_pln,
            total_value_pln=total_pln,
            net_in_pln=net_in_pln,
            pnl_pln=pnl_pln,
            pnl_pct=pnl_pct,
        ))

    # ── totals ──────────────────────────────────────────────────────────────
    total_mv = sum(r.market_value_pln for r in account_rows)
    total_cash = sum(r.cash_pln for r in account_rows)
    total_value = total_mv + total_cash
    global_pnl = total_value - net_in_global
    global_pnl_pct = (global_pnl / net_in_global * Decimal(100)) if net_in_global else None

    total_row = AccountRow(
        account_id="TOTAL",
        base_currency="PLN",
        market_value_native=total_mv,
        cash_native=total_cash,
        total_native=total_value,
        net_in_native=net_in_global,
        pnl_native=global_pnl,
        market_value_pln=total_mv,
        cash_pln=total_cash,
        total_value_pln=total_value,
        net_in_pln=net_in_global,
        pnl_pln=global_pnl,
        pnl_pct=global_pnl_pct,
    )

    # ── decomposition ───────────────────────────────────────────────────────
    # fx_on_cash is the residual; covers FX timing effects on realized P/L
    # and FX gains/losses on non-PLN cash revalued at current rates.
    components = unrealized_pln_total + realized_pln_total + dividends_pln + interest_pln + fees_pln + taxes_pln
    fx_on_cash_pln = global_pnl - components

    decomposition = Decomposition(
        unrealized_pln=unrealized_pln_total,
        realized_pln=realized_pln_total,
        dividends_pln=dividends_pln,
        interest_pln=interest_pln,
        fees_pln=fees_pln,
        taxes_pln=taxes_pln,
        fx_on_cash_pln=fx_on_cash_pln,
    )

    # ── XIRR ────────────────────────────────────────────────────────────────
    xirr: float | None = None
    if xirr_cashflows and total_value > 0:
        xirr_cashflows.append((as_of, float(total_value)))
        xirr = _xirr(xirr_cashflows)

    return SummaryResult(
        account_rows=account_rows,
        total=total_row,
        decomposition=decomposition,
        xirr=xirr,
        as_of=as_of,
        unpriced_symbols=unpriced,
    )
