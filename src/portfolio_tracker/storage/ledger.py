from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from portfolio_tracker.domain.events import Event


def append(events: list[Event], path: Path) -> int:
    """Append events to JSONL ledger. Returns count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            count += 1
    return count


def read(path: Path) -> Iterator[Event]:
    """Stream events from JSONL ledger."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield Event.from_dict(json.loads(line))
