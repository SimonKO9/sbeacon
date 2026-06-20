from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import rich.box
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="tracker", help="Portfolio tracker CLI", no_args_is_help=True)
load_app = typer.Typer(help="Load data from broker exports", no_args_is_help=True)
app.add_typer(load_app, name="load")
tax_app = typer.Typer(help="Tax estimation for PIT-38 (REGULAR pool)", no_args_is_help=True)
app.add_typer(tax_app, name="tax")
config_app = typer.Typer(help="Manage config.yaml (targets, instrument overrides)", no_args_is_help=True)
app.add_typer(config_app, name="config")
instruments_app = typer.Typer(help="Inspect held instruments and their auto-assigned tags", no_args_is_help=True)
app.add_typer(instruments_app, name="instruments")

console = Console()

_LEDGER = Path("data/ledger.jsonl")
_DB = Path("data/index.duckdb")
_PRICES_DB = Path("data/prices.duckdb")
_CONFIG = Path("data/config.yaml")


@load_app.command("xtb")
def load_xtb(
    paths: Annotated[list[Path], typer.Option("--paths", help="Directories or .xlsx files")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without writing")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    db: Annotated[Path, typer.Option(hidden=True)] = _DB,
) -> None:
    """Ingest XTB export files into the ledger."""
    from collections import Counter

    from portfolio_tracker.adapters.xtb.normalize import load
    from portfolio_tracker.storage import index as idx
    from portfolio_tracker.storage import ledger as ledger_mod

    events = load(paths, dry_run=dry_run)

    table = Table(title="XTB import" + (" (dry run)" if dry_run else ""))
    table.add_column("Event type")
    table.add_column("Count", justify="right")
    for etype, n in sorted(Counter(e.type.value for e in events).items()):
        table.add_row(etype, str(n))
    table.add_row("[bold]Total[/bold]", f"[bold]{len(events)}[/bold]")
    console.print(table)

    if dry_run:
        return

    known = idx.existing_ids(db)
    new_events = [e for e in events if e.id not in known]
    ledger_mod.append(new_events, ledger)
    idx.insert(new_events, db)
    console.print(f"[green]Written to {ledger} and {db}[/green]")


@load_app.command("ibkr")
def load_ibkr(
    paths: Annotated[list[Path], typer.Option("--paths", help="Directories or .csv files")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without writing")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    db: Annotated[Path, typer.Option(hidden=True)] = _DB,
) -> None:
    """Ingest IBKR Transaction History CSV export into the ledger."""
    from collections import Counter

    from portfolio_tracker.adapters.ibkr.normalize import load
    from portfolio_tracker.storage import index as idx
    from portfolio_tracker.storage import ledger as ledger_mod

    events = load(paths, dry_run=dry_run)

    table = Table(title="IBKR import" + (" (dry run)" if dry_run else ""))
    table.add_column("Event type")
    table.add_column("Count", justify="right")
    for etype, n in sorted(Counter(e.type.value for e in events).items()):
        table.add_row(etype, str(n))
    table.add_row("[bold]Total[/bold]", f"[bold]{len(events)}[/bold]")
    console.print(table)

    if dry_run:
        return

    known = idx.existing_ids(db)
    new_events = [e for e in events if e.id not in known]
    ledger_mod.append(new_events, ledger)
    idx.insert(new_events, db)
    console.print(f"[green]Written to {ledger} and {db}[/green]")


@app.command()
def accounts(db: Annotated[Path, typer.Option(hidden=True)] = _DB) -> None:
    """List loaded accounts."""
    import duckdb

    if not db.exists():
        console.print("[yellow]No index found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute(
        "SELECT account_id, type, COUNT(*) AS events"
        " FROM events GROUP BY account_id, type ORDER BY account_id"
    ).fetchall()
    con.close()

    table = Table(title="Accounts")
    table.add_column("Account ID")
    table.add_column("Event type")
    table.add_column("Events", justify="right")
    for row in rows:
        table.add_row(str(row[0]), str(row[1]), str(row[2]))
    console.print(table)


@app.command()
def positions(
    account: Annotated[str | None, typer.Option("--account", help="Filter by account ID")] = None,
    no_prices: Annotated[bool, typer.Option("--no-prices", help="Skip live price fetch")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    prices_db: Annotated[Path, typer.Option(hidden=True)] = _PRICES_DB,
) -> None:
    """Show open positions with current market prices."""
    from portfolio_tracker.domain.events import EventType
    from portfolio_tracker.pricing.cache import CachingProvider
    from portfolio_tracker.pricing.provider import Quote
    from portfolio_tracker.pricing.yahoo import YahooFinanceProvider
    from portfolio_tracker.reports.positions import compute_positions
    from portfolio_tracker.storage.ledger import read

    if not ledger.exists():
        console.print("[yellow]No ledger found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    events = [e for e in read(ledger) if e.type == EventType.TRADE]
    if account:
        events = [e for e in events if e.account_id == account]

    posns = compute_positions(events)

    if not posns:
        console.print("[yellow]No open positions found.[/yellow]")
        return

    from decimal import Decimal

    prices: dict[str, Quote] = {}
    fx_rates: dict[str, Decimal] = {}
    if not no_prices:
        provider = CachingProvider(YahooFinanceProvider(), prices_db, "yahoo")
        symbols = [p.symbol for p in posns]
        fx_pairs = ["USDPLN=X", "EURPLN=X", "GBPPLN=X"]
        with console.status("Fetching prices…"):
            all_quotes = provider.latest(symbols + fx_pairs)
        prices = {s: q for s, q in all_quotes.items() if s in set(symbols)}
        fx_rates = {
            "USD": all_quotes["USDPLN=X"].price if "USDPLN=X" in all_quotes else Decimal("1"),
            "EUR": all_quotes["EURPLN=X"].price if "EURPLN=X" in all_quotes else Decimal("1"),
            "GBP": all_quotes["GBPPLN=X"].price if "GBPPLN=X" in all_quotes else Decimal("1"),
        }

    posns = compute_positions(events, prices=prices, fx_rates=fx_rates if fx_rates else None)

    has_prices = bool(prices)
    has_pln = bool(fx_rates)

    table = Table(title="Open positions")
    table.add_column("Symbol")
    table.add_column("Account")
    table.add_column("Qty", justify="right")
    table.add_column("Avg cost", justify="right")
    if has_prices:
        table.add_column("Price", justify="right")
        table.add_column("Mkt value", justify="right")
        table.add_column("Unreal. P&L", justify="right")
    if has_pln:
        table.add_column("Mkt val PLN", justify="right")
        table.add_column("Unreal. PLN", justify="right")
        table.add_column("Weight %", justify="right")
    table.add_column("Ccy")

    def _pnl(val: Decimal | None) -> str:
        if val is None:
            return "—"
        color = "green" if val >= 0 else "red"
        return f"[{color}]{val:,.2f}[/{color}]"

    for pos in posns:
        row = [
            pos.symbol,
            pos.account_id,
            f"{pos.quantity:.4f}",
            f"{pos.avg_cost:.4f}",
        ]
        if has_prices:
            row.append(f"{pos.current_price:.4f}" if pos.current_price is not None else "—")
            row.append(f"{pos.market_value:,.2f}" if pos.market_value is not None else "—")
            row.append(_pnl(pos.unrealized_pnl))
        if has_pln:
            row.append(f"{pos.market_value_pln:,.0f}" if pos.market_value_pln is not None else "—")
            row.append(_pnl(pos.unrealized_pnl_pln))
            row.append(f"{pos.weight_pct:.1f}%" if pos.weight_pct is not None else "—")
        row.append(pos.quote_currency)
        table.add_row(*row)

    console.print(table)


@app.command()
def pnl(
    period: Annotated[str | None, typer.Option("--period", help="Year filter, e.g. 2025")] = None,
    from_date: Annotated[str | None, typer.Option("--from", help="Start date YYYY-MM-DD")] = None,
    to_date: Annotated[str | None, typer.Option("--to", help="End date YYYY-MM-DD")] = None,
    by: Annotated[str, typer.Option("--by", help="instrument|account|currency|wrapper|asset-class")] = "instrument",
    sort: Annotated[str, typer.Option("--sort", help="Sort by: total|realized|unrealized")] = "total",
    top: Annotated[int | None, typer.Option("--top", help="Show top N rows")] = None,
    no_prices: Annotated[bool, typer.Option("--no-prices", help="Skip live price fetch")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    prices_db: Annotated[Path, typer.Option(hidden=True)] = _PRICES_DB,
) -> None:
    """Show P/L by instrument, account, or other dimension."""
    import datetime as dt
    from decimal import Decimal

    from portfolio_tracker.pricing.cache import CachingProvider
    from portfolio_tracker.pricing.provider import Quote
    from portfolio_tracker.pricing.yahoo import YahooFinanceProvider
    from portfolio_tracker.reports.pnl import compute_pnl
    from portfolio_tracker.reports.positions import compute_positions
    from portfolio_tracker.domain.events import EventType
    from portfolio_tracker.storage.ledger import read

    if not ledger.exists():
        console.print("[yellow]No ledger found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    # Parse period bounds
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    if period:
        year = int(period)
        date_from = dt.date(year, 1, 1)
        date_to = dt.date(year, 12, 31)
    else:
        if from_date:
            date_from = dt.date.fromisoformat(from_date)
        if to_date:
            date_to = dt.date.fromisoformat(to_date)

    events = list(read(ledger))

    prices: dict[str, Quote] = {}
    fx_rates: dict[str, Decimal] = {}
    if not no_prices:
        trade_events = [e for e in events if e.type == EventType.TRADE]
        positions_stub = compute_positions(trade_events)
        symbols = list({p.symbol for p in positions_stub})
        fx_pairs = ["USDPLN=X", "EURPLN=X", "GBPPLN=X"]
        provider = CachingProvider(YahooFinanceProvider(), prices_db, "yahoo")
        with console.status("Fetching prices…"):
            all_quotes = provider.latest(symbols + fx_pairs)
        prices = {s: q for s, q in all_quotes.items() if s in set(symbols)}
        fx_rates = {
            "USD": all_quotes["USDPLN=X"].price if "USDPLN=X" in all_quotes else Decimal("1"),
            "EUR": all_quotes["EURPLN=X"].price if "EURPLN=X" in all_quotes else Decimal("1"),
            "GBP": all_quotes["GBPPLN=X"].price if "GBPPLN=X" in all_quotes else Decimal("1"),
        }

    result = compute_pnl(
        events,
        prices=prices,
        fx_rates=fx_rates,
        by=by,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort,
    )

    # ── table ───────────────────────────────────────────────────────────────
    is_period = result.period_label != "lifetime"
    col_label = by.capitalize() if by != "asset-class" else "Asset class"

    if by == "account":
        title = f"P/L by account — {result.period_label} (reporting: PLN)"
        if is_period:
            title += "\n[dim]realized/income = during period; unrealized = current snapshot[/dim]"
        table = Table(title=title, box=rich.box.ROUNDED)
        table.add_column(col_label)
        table.add_column("Realized", justify="right")
        table.add_column("Dividends", justify="right")
        table.add_column("Interest", justify="right")
        table.add_column("Fees", justify="right")
        table.add_column("Taxes", justify="right")
        table.add_column("Unrealized" + (" (now)" if is_period else ""), justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Return% (vs cost)", justify="right")
    else:
        title = f"P/L by {by} — {result.period_label} (reporting: PLN)"
        if is_period:
            title += "\n[dim]realized/income = during period; unrealized = current snapshot[/dim]"
        table = Table(title=title, box=rich.box.ROUNDED)
        table.add_column(col_label)
        table.add_column("Realized", justify="right")
        table.add_column("Unrealized" + (" (now)" if is_period else ""), justify="right")
        table.add_column("Income", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Return% (vs cost)", justify="right")

    def _signed(val: Decimal) -> str:
        if val == 0:
            return "—"
        sign = "+" if val > 0 else ""
        color = "green" if val > 0 else "red"
        return f"[{color}]{sign}{val:,.0f}[/{color}]"

    def _pct(val: Decimal | None) -> str:
        if val is None:
            return "—"
        sign = "+" if val > 0 else ""
        color = "green" if val > 0 else "red"
        return f"[{color}]{sign}{val:.1f}%[/{color}]"

    display_rows = result.rows[:top] if top else result.rows

    for r in display_rows:
        if by == "account":
            table.add_row(
                r.group_key,
                _signed(r.realized_pln),
                _signed(r.dividends_pln),
                _signed(r.interest_pln),
                _signed(r.fees_pln),
                _signed(r.taxes_pln),
                _signed(r.unrealized_pln),
                _signed(r.total_pln),
                _pct(r.total_return_pct),
            )
        else:
            table.add_row(
                r.group_key,
                _signed(r.realized_pln),
                _signed(r.unrealized_pln),
                _signed(r.income_pln),
                _signed(r.total_pln),
                _pct(r.total_return_pct),
            )

    # fx/cash residual row (lifetime only, non-zero)
    if result.period_label == "lifetime" and result.fx_cash_pln != 0:
        table.add_section()
        if by == "account":
            table.add_row("fx/cash", "—", "—", "—", "—", "—", "—", _signed(result.fx_cash_pln), "—")
        else:
            table.add_row("fx/cash", "—", "—", "—", _signed(result.fx_cash_pln), "—")

    # TOTAL row
    table.add_section()
    t = result.total
    grand_total = t.total_pln + result.fx_cash_pln
    total_pct = (grand_total / t.cost_basis_pln * Decimal(100)) if t.cost_basis_pln > 0 else None
    if by == "account":
        table.add_row(
            "[bold]TOTAL[/bold]",
            "[bold]" + _signed(t.realized_pln) + "[/bold]",
            "[bold]" + _signed(t.dividends_pln) + "[/bold]",
            "[bold]" + _signed(t.interest_pln) + "[/bold]",
            "[bold]" + _signed(t.fees_pln) + "[/bold]",
            "[bold]" + _signed(t.taxes_pln) + "[/bold]",
            "[bold]" + _signed(t.unrealized_pln) + "[/bold]",
            "[bold]" + _signed(grand_total) + "[/bold]",
            "[bold]" + _pct(total_pct) + "[/bold]",
        )
    else:
        table.add_row(
            "[bold]TOTAL[/bold]",
            "[bold]" + _signed(t.realized_pln) + "[/bold]",
            "[bold]" + _signed(t.unrealized_pln) + "[/bold]",
            "[bold]" + _signed(t.income_pln) + "[/bold]",
            "[bold]" + _signed(grand_total) + "[/bold]",
            "[bold]" + _pct(total_pct) + "[/bold]",
        )

    console.print(table)

    if result.unpriced_symbols:
        console.print(f"\n[yellow]⚠ No price for: {', '.join(result.unpriced_symbols)}[/yellow]")
    if result.period_label == "lifetime" and result.fx_cash_pln != 0:
        console.print("[dim]TOTAL ties to summary (includes fx/cash residual)[/dim]")


@app.command()
def summary(
    no_prices: Annotated[bool, typer.Option("--no-prices", help="Skip live price fetch")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    prices_db: Annotated[Path, typer.Option(hidden=True)] = _PRICES_DB,
    config_path: Annotated[Path, typer.Option(hidden=True)] = _CONFIG,
) -> None:
    """Show portfolio summary: value, net invested, P/L, XIRR."""
    from decimal import Decimal

    from portfolio_tracker.pricing.cache import CachingProvider
    from portfolio_tracker.pricing.provider import Quote
    from portfolio_tracker.pricing.yahoo import YahooFinanceProvider
    from portfolio_tracker.reports.positions import compute_positions
    from portfolio_tracker.reports.summary import compute_summary
    from portfolio_tracker.storage.ledger import read

    if not ledger.exists():
        console.print("[yellow]No ledger found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    events = list(read(ledger))

    prices: dict[str, Quote] = {}
    fx_rates: dict[str, Decimal] = {}
    if not no_prices:
        from portfolio_tracker.domain.events import EventType
        trade_events = [e for e in events if e.type == EventType.TRADE]
        positions_stub = compute_positions(trade_events)
        symbols = list({p.symbol for p in positions_stub})
        fx_pairs = ["USDPLN=X", "EURPLN=X", "GBPPLN=X"]
        provider = CachingProvider(YahooFinanceProvider(), prices_db, "yahoo")
        with console.status("Fetching prices…"):
            all_quotes = provider.latest(symbols + fx_pairs)
        prices = {s: q for s, q in all_quotes.items() if s in set(symbols)}
        fx_rates = {
            "USD": all_quotes["USDPLN=X"].price if "USDPLN=X" in all_quotes else Decimal("1"),
            "EUR": all_quotes["EURPLN=X"].price if "EURPLN=X" in all_quotes else Decimal("1"),
            "GBP": all_quotes["GBPPLN=X"].price if "GBPPLN=X" in all_quotes else Decimal("1"),
        }

    result = compute_summary(events, prices=prices, fx_rates=fx_rates)

    # ── account table ───────────────────────────────────────────────────────
    def _pnl_str(val: Decimal, pct: Decimal | None = None) -> str:
        sign = "+" if val >= 0 else ""
        color = "green" if val >= 0 else "red"
        pct_str = f"  [{color}]{sign}{pct:.1f}%[/{color}]" if pct is not None else ""
        return f"[{color}]{sign}{val:,.0f}[/{color}]{pct_str}"

    has_non_pln = any(r.base_currency != "PLN" for r in result.account_rows)

    table = Table(title=f"Portfolio summary — {result.as_of}  (reporting: PLN)", box=rich.box.ROUNDED)
    table.add_column("Account")
    table.add_column("Mkt value", justify="right")
    table.add_column("Cash", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Net in", justify="right")
    table.add_column("P/L", justify="right")
    table.add_column("Ccy")
    if has_non_pln:
        table.add_column("Total PLN", justify="right")
        table.add_column("Net in PLN", justify="right")
        table.add_column("P/L PLN", justify="right")
    table.add_column("P/L %", justify="right")

    def _pct_str(pct: Decimal | None) -> str:
        if pct is None:
            return "—"
        sign = "+" if pct >= 0 else ""
        color = "green" if pct >= 0 else "red"
        return f"[{color}]{sign}{pct:.1f}%[/{color}]"

    for row in result.account_rows:
        is_pln = row.base_currency == "PLN"
        r = [
            row.account_id,
            f"{row.market_value_native:,.0f}",
            f"{row.cash_native:,.2f}",
            f"{row.total_native:,.0f}",
            f"{row.net_in_native:,.0f}",
            _pnl_str(row.pnl_native),
            row.base_currency,
        ]
        if has_non_pln:
            r += [
                f"{row.total_value_pln:,.0f}" if not is_pln else "—",
                f"{row.net_in_pln:,.0f}" if not is_pln else "—",
                _pnl_str(row.pnl_pln) if not is_pln else "—",
            ]
        r.append(_pct_str(row.pnl_pct))
        table.add_row(*r)

    table.add_section()
    t = result.total
    total_row = [
        "[bold]TOTAL[/bold]",
        f"[bold]{t.market_value_pln:,.0f}[/bold]",
        f"[bold]{t.cash_pln:,.2f}[/bold]",
        f"[bold]{t.total_value_pln:,.0f}[/bold]",
        f"[bold]{t.net_in_pln:,.0f}[/bold]",
        "[bold]" + _pnl_str(t.pnl_pln) + "[/bold]",
        "[bold]PLN[/bold]",
    ]
    if has_non_pln:
        total_row += ["—", "—", "—"]
    total_row.append("[bold]" + _pct_str(t.pnl_pct) + "[/bold]")
    table.add_row(*total_row)

    console.print(table)

    # ── decomposition ───────────────────────────────────────────────────────
    d = result.decomposition

    def _signed(val: Decimal) -> str:
        sign = "+" if val >= 0 else ""
        color = "green" if val >= 0 else "red"
        return f"[{color}]{sign}{val:,.0f}[/{color}]"

    console.print()
    console.print("P/L decomposition (PLN):")
    console.print(
        f"  unrealized {_signed(d.unrealized_pln)}    "
        f"realized {_signed(d.realized_pln)}    "
        f"dividends {_signed(d.dividends_pln)}    "
        f"interest {_signed(d.interest_pln)}"
    )
    console.print(
        f"  fees {_signed(d.fees_pln)}    "
        f"taxes {_signed(d.taxes_pln)}    "
        f"fx/cash {_signed(d.fx_on_cash_pln)}"
    )

    # ── XIRR ────────────────────────────────────────────────────────────────
    console.print()
    if result.xirr is not None:
        xirr_pct = result.xirr * 100
        color = "green" if xirr_pct >= 0 else "red"
        simple_pct = float(t.pnl_pct) if t.pnl_pct is not None else 0.0
        console.print(
            f"Money-weighted return (XIRR): [{color}]{xirr_pct:+.1f}% p.a.[/{color}]"
            f"    (simple P/L: {simple_pct:+.1f}%)"
        )
    else:
        console.print("Money-weighted return (XIRR): [yellow]insufficient data[/yellow]")

    if result.unpriced_symbols:
        console.print(f"\n[yellow]⚠ No price for: {', '.join(result.unpriced_symbols)}[/yellow]")

    # ── net worth ────────────────────────────────────────────────────────────
    from portfolio_tracker.config import load_config

    cfg, generated = load_config(config_path)
    if generated:
        console.print(f"[yellow]Generated config at {config_path}[/yellow]")

    real_assets = cfg.extra_assets.get("real-assets", [])
    reserves    = cfg.extra_assets.get("reserves", [])

    if real_assets or reserves:
        def _to_pln(asset) -> Decimal:  # type: ignore[no-untyped-def]
            rate = fx_rates.get(asset.currency, Decimal("1")) if asset.currency != "PLN" else Decimal("1")
            return asset.value * rate

        portfolio_pln = result.total.total_value_pln
        real_pln  = [_to_pln(a) for a in real_assets]
        res_pln   = [_to_pln(a) for a in reserves]
        net_worth = portfolio_pln + sum(real_pln) + sum(res_pln)

        console.print()
        nw_table = Table(title="Net worth (PLN)", box=rich.box.SIMPLE, show_header=False, padding=(0, 1))
        nw_table.add_column("Category", style="")
        nw_table.add_column("Value PLN", justify="right")
        nw_table.add_column("Weight", justify="right")
        nw_table.add_column("Note", style="dim")

        def _pct(val: Decimal) -> str:
            return f"{val / net_worth * 100:.1f}%" if net_worth else "—"

        nw_table.add_row(
            "Investment portfolio",
            f"{portfolio_pln:,.0f}",
            _pct(portfolio_pln),
            "",
        )
        for asset, val in zip(real_assets, real_pln):
            nw_table.add_row("Real assets", f"{val:,.0f}", _pct(val), asset.name)
        for asset, val in zip(reserves, res_pln):
            nw_table.add_row("Reserves", f"{val:,.0f}", _pct(val), asset.name)

        nw_table.add_section()
        nw_table.add_row(
            "[bold]Net worth[/bold]",
            f"[bold]{net_worth:,.0f}[/bold]",
            "[bold]100.0%[/bold]",
            "",
        )
        console.print(nw_table)


# ── config subcommands ─────────────────────────────────────────────────────────

@config_app.command("path")
def config_path_cmd(config: Annotated[Path, typer.Option(hidden=True)] = _CONFIG) -> None:
    """Show the path to config.yaml."""
    console.print(str(config.resolve()))


@config_app.command("get")
def config_get(config: Annotated[Path, typer.Option(hidden=True)] = _CONFIG) -> None:
    """Print the full config (defaults merged with any edits)."""
    import yaml

    from portfolio_tracker.config import load_config

    cfg, generated = load_config(config)
    if generated:
        console.print(f"[yellow]Generated config at {config}[/yellow]")

    console.print(yaml.dump({
        "reporting_currency": cfg.reporting_currency,
        "targets": cfg.targets,
        "instruments": cfg.instruments,
    }, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip())


@config_app.command("get-allocations")
def config_get_allocations(
    config: Annotated[Path, typer.Option(hidden=True)] = _CONFIG,
) -> None:
    """Show target allocations: raw weight and normalized %."""
    from portfolio_tracker.config import load_config, normalized_targets

    cfg, generated = load_config(config)
    if generated:
        console.print(f"[yellow]Generated config at {config}[/yellow]")

    norm = normalized_targets(cfg)
    table = Table(title="Target allocations", box=rich.box.ROUNDED)
    table.add_column("Role")
    table.add_column("Raw", justify="right")
    table.add_column("Target %", justify="right")

    raw_total = 0.0
    for role, raw in cfg.targets.items():
        pct = norm.get(role, 0.0) * 100
        table.add_row(role, f"{raw:.2f}", f"{pct:.1f}%")
        raw_total += raw

    table.add_section()
    table.add_row("[bold]TOTAL[/bold]", f"[bold]{raw_total:.2f}[/bold]", "[bold]100.0%[/bold]")
    console.print(table)


@config_app.command("set-allocation")
def config_set_allocation(
    role: Annotated[str, typer.Argument(help="Role name, e.g. core")],
    weight: Annotated[float, typer.Argument(help="Raw weight, e.g. 0.4")],
    config: Annotated[Path, typer.Option(hidden=True)] = _CONFIG,
) -> None:
    """Set a target allocation weight for a role."""
    from portfolio_tracker.config import load_config, save_config, set_allocation

    cfg, generated = load_config(config)
    if generated:
        console.print(f"[yellow]Generated config at {config}[/yellow]")

    try:
        set_allocation(cfg, role, weight)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    save_config(cfg, config)
    console.print(f"[green]Set {role} = {weight}[/green]")


@config_app.command("set-instrument-role")
def config_set_instrument_role(
    ticker: Annotated[str, typer.Argument(help="Ticker, e.g. FOO.PL")],
    role: Annotated[str, typer.Argument(help="Role, e.g. satellite")],
    config: Annotated[Path, typer.Option(hidden=True)] = _CONFIG,
) -> None:
    """Override the role for an instrument."""
    from portfolio_tracker.config import load_config, save_config, set_instrument_role

    cfg, generated = load_config(config)
    if generated:
        console.print(f"[yellow]Generated config at {config}[/yellow]")

    try:
        set_instrument_role(cfg, ticker, role)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    save_config(cfg, config)
    console.print(f"[green]Set {ticker} role = {role}[/green]")


@config_app.command("set-instrument-asset-class")
def config_set_instrument_asset_class(
    ticker: Annotated[str, typer.Argument(help="Ticker, e.g. CDR.PL")],
    asset_class: Annotated[str, typer.Argument(help="Asset class, e.g. equity")],
    config: Annotated[Path, typer.Option(hidden=True)] = _CONFIG,
) -> None:
    """Override the asset class for an instrument."""
    from portfolio_tracker.config import load_config, save_config, set_instrument_asset_class

    cfg, generated = load_config(config)
    if generated:
        console.print(f"[yellow]Generated config at {config}[/yellow]")

    try:
        set_instrument_asset_class(cfg, ticker, asset_class)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    save_config(cfg, config)
    console.print(f"[green]Set {ticker} asset_class = {asset_class}[/green]")


@config_app.command("clear-instrument")
def config_clear_instrument(
    ticker: Annotated[str, typer.Argument(help="Ticker whose overrides to remove")],
    config: Annotated[Path, typer.Option(hidden=True)] = _CONFIG,
) -> None:
    """Remove manual overrides for an instrument (reverts to auto-discovery)."""
    from portfolio_tracker.config import clear_instrument, load_config, save_config

    cfg, generated = load_config(config)
    if generated:
        console.print(f"[yellow]Generated config at {config}[/yellow]")

    clear_instrument(cfg, ticker)
    save_config(cfg, config)
    console.print(f"[green]Cleared overrides for {ticker}[/green]")


# ── instruments subcommands ────────────────────────────────────────────────────

@instruments_app.command("list")
def instruments_list(
    review: Annotated[bool, typer.Option("--review", help="Show only low-confidence / untagged")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    config: Annotated[Path, typer.Option(hidden=True)] = _CONFIG,
) -> None:
    """List held instruments with their auto-assigned tags."""
    from portfolio_tracker.config import load_config
    from portfolio_tracker.reports.allocation import get_tagged_instruments
    from portfolio_tracker.storage.ledger import read

    if not ledger.exists():
        console.print("[yellow]No ledger found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    cfg, generated = load_config(config)
    if generated:
        console.print(f"[yellow]Generated config at {config}[/yellow]")

    events = list(read(ledger))
    tagged = get_tagged_instruments(events, cfg.instruments)

    if review:
        tagged = [t for t in tagged if t.source in ("review", "untagged")]

    if not tagged:
        console.print("[dim]No instruments to display.[/dim]")
        return

    _SOURCE_STYLE: dict[str, str] = {
        "auto": "dim",
        "manual": "green",
        "review": "yellow",
        "untagged": "red",
    }

    table = Table(
        title="Instruments — effective tags" + (" (needs review)" if review else ""),
        box=rich.box.ROUNDED,
    )
    table.add_column("Ticker")
    table.add_column("Name")
    table.add_column("Asset class")
    table.add_column("Role")
    table.add_column("Source")

    for t in tagged:
        style = _SOURCE_STYLE.get(t.source, "")
        ac_str = t.asset_class.value if t.asset_class else "—"
        role_str = t.role.value if t.role else "—"
        if t.source == "review":
            role_str += "?"
        table.add_row(
            t.ticker,
            t.name or "—",
            ac_str,
            role_str,
            f"[{style}]{t.source}[/{style}]" if style else t.source,
        )

    console.print(table)


# ── allocation command ─────────────────────────────────────────────────────────

@app.command()
def allocation(
    by: Annotated[str, typer.Option("--by", help="role|asset-class")] = "role",
    ex_cash: Annotated[bool, typer.Option("--ex-cash", help="Exclude cash from weights")] = False,
    no_prices: Annotated[bool, typer.Option("--no-prices", help="Skip live price fetch")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    prices_db: Annotated[Path, typer.Option(hidden=True)] = _PRICES_DB,
    config_path: Annotated[Path, typer.Option(hidden=True)] = _CONFIG,
) -> None:
    """Show portfolio allocation by role or asset class."""
    from decimal import Decimal

    from portfolio_tracker.config import load_config
    from portfolio_tracker.domain.events import EventType
    from portfolio_tracker.pricing.cache import CachingProvider
    from portfolio_tracker.pricing.yahoo import YahooFinanceProvider
    from portfolio_tracker.reports.allocation import compute_allocation
    from portfolio_tracker.reports.positions import compute_positions
    from portfolio_tracker.storage.ledger import read

    if by not in ("role", "asset-class"):
        console.print("[red]--by must be 'role' or 'asset-class'[/red]")
        raise typer.Exit(1)

    if not ledger.exists():
        console.print("[yellow]No ledger found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    cfg, generated = load_config(config_path)
    if generated:
        console.print(f"[yellow]Generated config at {config_path}[/yellow]")

    events = list(read(ledger))

    prices: dict = {}
    fx_rates: dict[str, Decimal] = {}
    if not no_prices:
        trade_events = [e for e in events if e.type == EventType.TRADE]
        positions_stub = compute_positions(trade_events)
        symbols = list({p.symbol for p in positions_stub})
        fx_pairs = ["USDPLN=X", "EURPLN=X", "GBPPLN=X"]
        provider = CachingProvider(YahooFinanceProvider(), prices_db, "yahoo")
        with console.status("Fetching prices…"):
            all_quotes = provider.latest(symbols + fx_pairs)
        prices = {s: q for s, q in all_quotes.items() if s in set(symbols)}
        fx_rates = {
            "USD": all_quotes["USDPLN=X"].price if "USDPLN=X" in all_quotes else Decimal("1"),
            "EUR": all_quotes["EURPLN=X"].price if "EURPLN=X" in all_quotes else Decimal("1"),
            "GBP": all_quotes["GBPPLN=X"].price if "GBPPLN=X" in all_quotes else Decimal("1"),
        }

    result = compute_allocation(
        events, prices=prices, fx_rates=fx_rates, config=cfg, by=by, ex_cash=ex_cash
    )

    cash_label = "ex-cash" if ex_cash else "incl. cash"
    title = f"Allocation by {by} — {result.total_pln:,.0f} PLN ({cash_label})"
    has_targets = result.lens == "role"

    table = Table(title=title, box=rich.box.ROUNDED)
    table.add_column("Bucket")
    table.add_column("Value PLN", justify="right")
    table.add_column("Weight", justify="right")
    if has_targets:
        table.add_column("Target", justify="right")
        table.add_column("Drift", justify="right")
        table.add_column("Rebalance PLN", justify="right")

    def _drift_str(drift: Decimal) -> str:
        pp = drift * 100
        sign = "+" if pp > 0 else ""
        color = "red" if abs(pp) > Decimal("2") else "dim"
        return f"[{color}]{sign}{pp:.1f}pp[/{color}]"

    def _rebalance_str(rebalance: Decimal) -> str:
        sign = "+" if rebalance > 0 else ""
        color = "green" if rebalance > 0 else "red"
        return f"[{color}]{sign}{rebalance:,.0f}[/{color}]"

    for row in result.rows:
        w_str = f"{row.weight * 100:.1f}%"
        r: list[str] = [row.bucket, f"{row.value_pln:,.0f}", w_str]
        if has_targets and row.target is not None:
            r.append(f"{row.target * 100:.0f}%")
            r.append(_drift_str(row.drift) if row.drift is not None else "—")
            r.append(_rebalance_str(row.rebalance) if row.rebalance is not None else "—")
        elif has_targets:
            r += ["—", "—", "—"]
        table.add_row(*r)

    table.add_section()
    total_row = [
        "[bold]TOTAL[/bold]",
        f"[bold]{result.total_pln:,.0f}[/bold]",
        "[bold]100.0%[/bold]",
    ]
    if has_targets:
        total_row += ["", "", ""]
    table.add_row(*total_row)

    console.print(table)

    if result.untagged_pln > 0:
        console.print(
            f"\n[yellow]untagged  {result.untagged_pln:,.0f} PLN — "
            "run 'tracker instruments list --review' to fix[/yellow]"
        )

    if has_targets:
        console.print(
            "\n[dim]Rebalancing in a REGULAR account is a taxable event. "
            "Run 'tracker tax harvest' before acting.[/dim]"
        )


# ── tax subcommands ────────────────────────────────────────────────────────────

def _tax_prices_and_fx(
    no_prices: bool,
    events: list,
    prices_db: Path,
) -> tuple[dict, dict]:
    from decimal import Decimal
    from portfolio_tracker.domain.events import EventType
    from portfolio_tracker.pricing.cache import CachingProvider
    from portfolio_tracker.pricing.yahoo import YahooFinanceProvider
    from portfolio_tracker.reports.positions import compute_positions
    from portfolio_tracker.pricing.provider import Quote

    prices: dict[str, Quote] = {}
    fx_rates: dict[str, Decimal] = {}
    if not no_prices:
        trade_events = [e for e in events if e.type == EventType.TRADE]
        positions_stub = compute_positions(trade_events)
        symbols = list({p.symbol for p in positions_stub})
        fx_pairs = ["USDPLN=X", "EURPLN=X", "GBPPLN=X"]
        provider = CachingProvider(YahooFinanceProvider(), prices_db, "yahoo")
        with console.status("Fetching prices…"):
            all_quotes = provider.latest(symbols + fx_pairs)
        prices = {s: q for s, q in all_quotes.items() if s in set(symbols)}
        fx_rates = {
            "USD": all_quotes["USDPLN=X"].price if "USDPLN=X" in all_quotes else Decimal("1"),
            "EUR": all_quotes["EURPLN=X"].price if "EURPLN=X" in all_quotes else Decimal("1"),
            "GBP": all_quotes["GBPPLN=X"].price if "GBPPLN=X" in all_quotes else Decimal("1"),
        }
    return prices, fx_rates


def _estimate_banner() -> None:
    console.print(
        "[yellow bold]⚠ estimate[/yellow bold] — transacted FX (not NBP D-1); "
        "not filing-grade. Confirm with an advisor before filing PIT-38."
    )


def _nbp_fx_fn(prices_db: Path):  # type: ignore[return]
    """Return a callable (currency, trade_date) → PLN rate using cached NBP D-1 rates."""
    from portfolio_tracker.pricing.nbp import CachedNBPProvider
    nbp = CachedNBPProvider(prices_db)

    def fx_fn(currency: str, trade_date) -> "Decimal":  # noqa: F821
        from decimal import Decimal
        if currency == "PLN":
            return Decimal("1")
        return nbp.rate(currency, "PLN", trade_date)

    return fx_fn


@tax_app.command("summary")
def tax_summary(
    year: Annotated[int | None, typer.Option("--year", help="Tax year (default: current)")] = None,
    no_prices: Annotated[bool, typer.Option("--no-prices")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    prices_db: Annotated[Path, typer.Option(hidden=True)] = _PRICES_DB,
) -> None:
    """PIT-38 capital-gains roll-up: proceeds, cost, deductible costs, net gain, tax @19%."""
    import datetime as dt
    from decimal import Decimal
    from portfolio_tracker.reports.tax import compute_tax_summary
    from portfolio_tracker.storage.ledger import read

    if not ledger.exists():
        console.print("[yellow]No ledger found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    tax_year = year or dt.date.today().year
    events = list(read(ledger))

    fx_fn = None if no_prices else _nbp_fx_fn(prices_db)
    with console.status("Fetching NBP D-1 rates…") if fx_fn else console.status(""):
        result = compute_tax_summary(events, fx_rates={}, year=tax_year, fx_fn=fx_fn)

    if result.estimate:
        _estimate_banner()
    console.print()
    console.print(
        f"[bold]Tax {tax_year}[/bold] — REGULAR pool (PLN/EUR/USD), "
        f"{'estimate mode' if result.estimate else 'NBP D-1'}"
    )
    console.print()

    def _pln(val: Decimal) -> str:
        sign = "+" if val > 0 else ""
        return f"{sign}{val:,.0f} PLN"

    console.print("Capital gains (PIT-38)")
    console.print(f"  realized proceeds (PLN)    {result.proceeds_pln:>14,.0f}")
    console.print(f"  realized cost basis (PLN)  {result.cost_basis_pln:>14,.0f}")
    if result.deductible_costs_pln:
        console.print(f"  deductible costs           {-result.deductible_costs_pln:>14,.0f}")
    console.print("  " + "─" * 38)

    gain_color = "green" if result.net_gain_pln >= 0 else "red"
    gain_str = _pln(result.net_gain_pln)
    console.print(f"  net realized gain          [{gain_color}]{gain_str:>14}[/{gain_color}]")

    tax_color = "red" if result.tax_pln > 0 else "dim"
    console.print(f"  tax @ 19%                  [{tax_color}]{result.tax_pln:>14,.0f} PLN[/{tax_color}]")

    if not result.disposals:
        console.print(f"\n[dim]No disposals in {tax_year}.[/dim]")


@tax_app.command("gains")
def tax_gains(
    year: Annotated[int | None, typer.Option("--year", help="Tax year (default: current)")] = None,
    no_prices: Annotated[bool, typer.Option("--no-prices")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    prices_db: Annotated[Path, typer.Option(hidden=True)] = _PRICES_DB,
) -> None:
    """Itemized realized disposals (per closed FIFO lot) for the tax year."""
    import datetime as dt
    from decimal import Decimal
    from portfolio_tracker.reports.tax import compute_tax_summary
    from portfolio_tracker.storage.ledger import read

    if not ledger.exists():
        console.print("[yellow]No ledger found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    tax_year = year or dt.date.today().year
    events = list(read(ledger))

    fx_fn = None if no_prices else _nbp_fx_fn(prices_db)
    with console.status("Fetching NBP D-1 rates…") if fx_fn else console.status(""):
        result = compute_tax_summary(events, fx_rates={}, year=tax_year, fx_fn=fx_fn)

    if result.estimate:
        _estimate_banner()

    if not result.disposals:
        console.print(f"\n[dim]No disposals in {tax_year}.[/dim]")
        return

    table = Table(
        title=f"Realized disposals {tax_year} — REGULAR pool (PLN)",
        box=rich.box.ROUNDED,
    )
    table.add_column("Symbol")
    table.add_column("Sell date")
    table.add_column("Qty", justify="right")
    table.add_column("Proceeds PLN", justify="right")
    table.add_column("Cost PLN", justify="right")
    table.add_column("Gain PLN", justify="right")

    def _gain_str(val: Decimal) -> str:
        sign = "+" if val > 0 else ""
        color = "green" if val > 0 else ("red" if val < 0 else "")
        s = f"{sign}{val:,.0f}"
        return f"[{color}]{s}[/{color}]" if color else s

    for d in result.disposals:
        table.add_row(
            d.symbol,
            str(d.sell_date),
            f"{d.quantity:.4f}",
            f"{d.proceeds_pln:,.0f}",
            f"{d.cost_pln:,.0f}",
            _gain_str(d.gain_pln),
        )

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]", "", "",
        f"[bold]{result.proceeds_pln:,.0f}[/bold]",
        f"[bold]{result.cost_basis_pln:,.0f}[/bold]",
        "[bold]" + _gain_str(result.net_gain_pln + result.deductible_costs_pln) + "[/bold]",
    )
    console.print(table)

    if result.deductible_costs_pln:
        console.print(
            f"[dim]Deductible costs: {result.deductible_costs_pln:,.0f} PLN"
            f" → net gain: {result.net_gain_pln:,.0f} PLN[/dim]"
        )


@tax_app.command("harvest")
def tax_harvest_cmd(
    year: Annotated[int | None, typer.Option("--year", help="Tax year (default: current)")] = None,
    no_prices: Annotated[bool, typer.Option("--no-prices")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    prices_db: Annotated[Path, typer.Option(hidden=True)] = _PRICES_DB,
) -> None:
    """Loss-harvesting candidates: positions where crystallising loss offsets gains."""
    import datetime as dt
    from portfolio_tracker.reports.tax import compute_tax_harvest
    from portfolio_tracker.storage.ledger import read

    if not ledger.exists():
        console.print("[yellow]No ledger found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    tax_year = year or dt.date.today().year
    events = list(read(ledger))
    prices, fx_rates = _tax_prices_and_fx(no_prices, events, prices_db)

    fx_fn = None if no_prices else _nbp_fx_fn(prices_db)
    with console.status("Fetching NBP D-1 rates…") if fx_fn else console.status(""):
        result = compute_tax_harvest(events, prices=prices, fx_rates=fx_rates, year=tax_year, fx_fn=fx_fn)

    if result.estimate:
        _estimate_banner()
    console.print()
    console.print(f"[bold]Loss harvesting {tax_year}[/bold] — REGULAR pool")
    gain_color = "green" if result.ytd_gain_pln >= 0 else "red"
    console.print(
        f"YTD realized gain (tax basis): "
        f"[{gain_color}]{result.ytd_gain_pln:+,.0f} PLN[/{gain_color}]"
        f"   →  tax due @19%: [red]{result.ytd_tax_due_pln:,.0f} PLN[/red]"
    )
    console.print(f"Days to year-end: {result.days_to_year_end}")
    console.print()

    if not result.candidates:
        console.print("[dim]No loss candidates in REGULAR pool at current prices.[/dim]")
        return

    from decimal import Decimal

    table = Table(box=rich.box.ROUNDED)
    table.add_column("Candidate")
    table.add_column("Qty", justify="right")
    table.add_column("FIFO loss if sold (PLN)", justify="right")
    table.add_column("Tax saved @19%", justify="right")
    table.add_column("Cumulative offset", justify="right")

    for c in result.candidates:
        table.add_row(
            c.symbol,
            f"{c.quantity:.4f}",
            f"[red]{c.fifo_loss_pln:,.0f}[/red]",
            f"[green]{c.tax_saved_pln:,.0f}[/green]",
            f"[red]{c.cumulative_offset_pln:,.0f}[/red]",
        )

    console.print(table)
    console.print()
    console.print(
        "[dim]Poland has no wash-sale rule — a losing position may be sold and immediately "
        "rebought to crystallise the loss. See 'tax lots <instrument>' before sizing a partial "
        "harvest. Deferral, not elimination — rebuy resets cost basis lower.[/dim]"
    )


@tax_app.command("lots")
def tax_lots_cmd(
    instrument: Annotated[str, typer.Argument(help="Instrument symbol, e.g. AAPL.US")],
    no_prices: Annotated[bool, typer.Option("--no-prices")] = False,
    ledger: Annotated[Path, typer.Option(hidden=True)] = _LEDGER,
    prices_db: Annotated[Path, typer.Option(hidden=True)] = _PRICES_DB,
) -> None:
    """FIFO lot / tax-basis inspection for one holding in the REGULAR pool."""
    from portfolio_tracker.reports.tax import compute_tax_lots
    from portfolio_tracker.storage.ledger import read

    if not ledger.exists():
        console.print("[yellow]No ledger found — run 'tracker load xtb' first.[/yellow]")
        raise typer.Exit(1)

    events = list(read(ledger))
    prices, fx_rates = _tax_prices_and_fx(no_prices, events, prices_db)

    fx_fn = None if no_prices else _nbp_fx_fn(prices_db)
    with console.status("Fetching NBP D-1 rates…") if fx_fn else console.status(""):
        result = compute_tax_lots(events, instrument, prices=prices, fx_rates=fx_rates, fx_fn=fx_fn)

    if result.estimate:
        _estimate_banner()

    if not result.lots:
        console.print(f"\n[yellow]No open REGULAR lots found for {instrument}.[/yellow]")
        return

    has_price = result.lots[0].current_price_pln is not None

    table = Table(
        title=f"FIFO lots: {instrument} — REGULAR pool (tax basis PLN)",
        box=rich.box.ROUNDED,
    )
    table.add_column("#", justify="right")
    table.add_column("Buy date")
    table.add_column("Qty", justify="right")
    table.add_column("Cost/unit PLN", justify="right")
    table.add_column("Total cost PLN", justify="right")
    if has_price:
        table.add_column("Price PLN", justify="right")
        table.add_column("Gain if sold PLN", justify="right")

    from decimal import Decimal

    def _gain_str(val: Decimal | None) -> str:
        if val is None:
            return "—"
        sign = "+" if val > 0 else ""
        color = "green" if val > 0 else ("red" if val < 0 else "")
        s = f"{sign}{val:,.0f}"
        return f"[{color}]{s}[/{color}]" if color else s

    for i, lot in enumerate(result.lots, 1):
        row = [
            str(i),
            str(lot.buy_date),
            f"{lot.quantity:.4f}",
            f"{lot.cost_per_unit_pln:,.2f}",
            f"{lot.total_cost_pln:,.0f}",
        ]
        if has_price:
            row.append(f"{lot.current_price_pln:,.2f}" if lot.current_price_pln else "—")
            row.append(_gain_str(lot.unrealized_pln))
        table.add_row(*row)

    table.add_section()
    total_row = ["", "[bold]TOTAL[/bold]", "", "", f"[bold]{result.total_cost_pln:,.0f}[/bold]"]
    if has_price:
        total_row += ["", "[bold]" + _gain_str(result.total_unrealized_pln) + "[/bold]"]
    table.add_row(*total_row)

    console.print(table)
