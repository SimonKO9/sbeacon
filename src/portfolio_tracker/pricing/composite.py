from __future__ import annotations

import logging
from datetime import date

from .provider import Bar, PriceProvider, Quote

logger = logging.getLogger(__name__)


class CompositeProvider:
    """Tries each provider in order; falls back on failure or missing symbols."""

    def __init__(self, providers: list[PriceProvider]) -> None:
        self._providers = providers

    def latest(self, symbols: list[str]) -> dict[str, Quote]:
        remaining = list(symbols)
        result: dict[str, Quote] = {}
        for provider in self._providers:
            if not remaining:
                break
            try:
                fetched = provider.latest(remaining)
                result.update(fetched)
                remaining = [s for s in remaining if s not in fetched]
            except Exception:
                logger.warning("Provider %s failed", provider, exc_info=True)
        return result

    def history(self, symbol: str, start: date, end: date) -> list[Bar]:
        for provider in self._providers:
            try:
                return provider.history(symbol, start, end)
            except Exception:
                logger.warning("Provider %s failed for history(%s)", provider, symbol, exc_info=True)
        return []
