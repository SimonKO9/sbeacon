from __future__ import annotations

from pathlib import Path

import duckdb

from portfolio_tracker.domain.events import Event

_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id VARCHAR PRIMARY KEY,
    account_id VARCHAR NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    type VARCHAR NOT NULL,
    amount DOUBLE NOT NULL,
    currency VARCHAR(10) NOT NULL,
    symbol VARCHAR,
    quantity DOUBLE,
    price DOUBLE,
    fees DOUBLE NOT NULL DEFAULT 0,
    instrument_name VARCHAR,
    isin VARCHAR,
    source_file VARCHAR,
    source_row INTEGER
)
"""


def _open(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(_DDL)
    return con


def insert(events: list[Event], db_path: Path) -> int:
    """Insert events, skipping duplicates by id. Returns number of events attempted."""
    con = _open(db_path)
    try:
        for event in events:
            con.execute(
                """
                INSERT INTO events
                    (id, account_id, timestamp, type, amount, currency, symbol,
                     quantity, price, fees, instrument_name, isin, source_file, source_row)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
                """,
                [
                    event.id,
                    event.account_id,
                    event.timestamp,
                    event.type.value,
                    float(event.amount),
                    event.currency,
                    event.instrument.symbol if event.instrument else None,
                    float(event.quantity) if event.quantity is not None else None,
                    float(event.price) if event.price is not None else None,
                    float(event.fees),
                    event.instrument.name if event.instrument else None,
                    event.instrument.isin if event.instrument else None,
                    str(event.source.file),
                    event.source.row,
                ],
            )
        con.commit()
    finally:
        con.close()
    return len(events)


def existing_ids(db_path: Path) -> set[str]:
    """Return the set of event ids already in the index."""
    if not db_path.exists():
        return set()
    con = _open(db_path)
    try:
        rows = con.execute("SELECT id FROM events").fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def rebuild(ledger_path: Path, db_path: Path) -> int:
    """Drop and rebuild the index from the canonical ledger."""
    from portfolio_tracker.storage.ledger import read

    if db_path.exists():
        db_path.unlink()
    return insert(list(read(ledger_path)), db_path)
