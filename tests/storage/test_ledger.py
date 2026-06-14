from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from portfolio_tracker.domain.events import Event
from portfolio_tracker.storage.ledger import append, read


def test_round_trip(tmp_path: Path, sample_event: Event) -> None:
    ledger = tmp_path / "ledger.jsonl"
    append([sample_event], ledger)
    events = list(read(ledger))
    assert len(events) == 1
    assert events[0].id == sample_event.id
    assert events[0].amount == sample_event.amount
    assert events[0].timestamp == sample_event.timestamp


def test_append_is_additive(tmp_path: Path, sample_event: Event) -> None:
    ledger = tmp_path / "ledger.jsonl"
    append([sample_event], ledger)
    append([sample_event], ledger)
    # Ledger itself allows duplicates; the DuckDB index handles dedup
    assert len(list(read(ledger))) == 2


def test_read_nonexistent_returns_empty(tmp_path: Path) -> None:
    assert list(read(tmp_path / "missing.jsonl")) == []


def test_decimal_survives_json_round_trip(tmp_path: Path, sample_event: Event) -> None:
    ledger = tmp_path / "ledger.jsonl"
    append([sample_event], ledger)
    restored = next(read(ledger))
    assert isinstance(restored.amount, Decimal)
    assert restored.amount == Decimal("-10250.00")


def test_append_returns_count(tmp_path: Path, sample_event: Event) -> None:
    ledger = tmp_path / "ledger.jsonl"
    assert append([sample_event, sample_event], ledger) == 2
