from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="tracker", help="Portfolio tracker CLI", no_args_is_help=True)
load_app = typer.Typer(help="Load data from broker exports", no_args_is_help=True)
app.add_typer(load_app, name="load")

console = Console()

_LEDGER = Path("data/ledger.jsonl")
_DB = Path("data/index.duckdb")


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

    ledger_mod.append(events, ledger)
    idx.insert(events, db)
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
def positions() -> None:
    """Show open positions (not yet implemented)."""
    console.print("[yellow]Not yet implemented.[/yellow]")


@app.command()
def pnl() -> None:
    """Show realized and unrealized P/L (not yet implemented)."""
    console.print("[yellow]Not yet implemented.[/yellow]")


@app.command()
def summary() -> None:
    """Show portfolio summary (not yet implemented)."""
    console.print("[yellow]Not yet implemented.[/yellow]")
