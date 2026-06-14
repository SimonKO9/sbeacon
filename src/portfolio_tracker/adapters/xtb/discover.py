from __future__ import annotations

import logging
from pathlib import Path

from portfolio_tracker.domain.accounts import Account, Wrapper

logger = logging.getLogger(__name__)

ACCOUNT_MAP: dict[str, tuple[Wrapper, str]] = {
    "PLN": (Wrapper.REGULAR, "PLN"),
    "EUR": (Wrapper.REGULAR, "EUR"),
    "USD": (Wrapper.REGULAR, "USD"),
    "IKE": (Wrapper.IKE, "PLN"),
    "IKZE": (Wrapper.IKZE, "PLN"),
}

BROKER = "XTB"


def discover(paths: list[Path]) -> list[tuple[Path, Account]]:
    """Walk paths recursively, match .xlsx files to accounts by filename prefix."""
    results: list[tuple[Path, Account]] = []
    for path in paths:
        candidates = sorted(path.rglob("*.xlsx")) if path.is_dir() else [path]
        for xlsx in candidates:
            prefix = xlsx.stem.split("_")[0]
            if prefix not in ACCOUNT_MAP:
                logger.warning("Skipping %s: unrecognized prefix %r", xlsx.name, prefix)
                continue
            wrapper, base_currency = ACCOUNT_MAP[prefix]
            account = Account(
                account_id=f"{BROKER}_{prefix}",
                broker=BROKER,
                wrapper=wrapper,
                base_currency=base_currency,
            )
            results.append((xlsx, account))
    return results
