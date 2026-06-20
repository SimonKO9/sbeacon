from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import requests
import yaml

from .provider import Bar, Quote

logger = logging.getLogger(__name__)

_BASE = "https://query2.finance.yahoo.com/v8/finance/chart"
_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
_HEADERS = {"User-Agent": "Mozilla/5.0"}

# Yahoo Finance exchange code → ISO currency.  Used to pick the right listing
# when a bare symbol (no exchange suffix) matches multiple equities.
_EXCHANGE_CURRENCY: dict[str, str] = {
    "AMS": "EUR", "FRA": "EUR", "PAR": "EUR", "MIL": "EUR",
    "MCE": "EUR", "VIE": "EUR", "HEL": "EUR", "EBR": "EUR",
    "GER": "EUR",  # Xetra (Deutsche Börse)
    "NYQ": "USD", "NMS": "USD", "NGS": "USD", "PCX": "USD", "BTS": "USD",
    "LSE": "GBP",
    "CPH": "DKK",
    "STO": "SEK", "NGM": "SEK",
    "OSL": "NOK",
    "ZUR": "CHF",
    "WAR": "PLN",
}

# For symbols that Yahoo search doesn't surface on the right exchange, probe
# these suffixes in order.  Stops at the first ticker whose currency matches.
_CURRENCY_SUFFIX_PROBES: dict[str, list[str]] = {
    "EUR": [".DE", ".AS", ".PA", ".MI", ".MC"],
    "GBP": [".L"],
    "DKK": [".CO"],
    "SEK": [".ST"],
    "NOK": [".OL"],
    "PLN": [".WA"],
    "CHF": [".SW"],
}


def _probe_suffix(symbol: str, currency: str, timeout: int = 10) -> str | None:
    """Try common exchange suffixes for *currency*; return the first ticker that
    resolves and whose Yahoo-reported currency matches."""
    for suffix in _CURRENCY_SUFFIX_PROBES.get(currency, []):
        candidate = symbol + suffix
        try:
            resp = requests.get(
                f"{_BASE}/{candidate}",
                params={"interval": "1d", "range": "1d"},
                headers=_HEADERS,
                timeout=timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("chart", {}).get("result") or []
            if not results:
                continue
            meta = results[0]["meta"]
            if meta.get("currency") == currency and meta.get("regularMarketPrice") is not None:
                logger.info("yahoo suffix probe: %r + %s → %s", symbol, currency, candidate)
                return candidate
        except Exception:
            continue
    return None


def _yahoo_search(query: str, timeout: int = 10) -> list[dict]:
    """Return equity quotes from Yahoo Finance search API for *query*."""
    try:
        resp = requests.get(
            _SEARCH_URL,
            params={"q": query, "quotesCount": 10, "newsCount": 0},
            headers=_HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception:
        logger.warning("yahoo search: request failed for %r", query, exc_info=True)
        return []
    return [q for q in resp.json().get("quotes", []) if q.get("quoteType") == "EQUITY"]


def _matches_currency(quote: dict, currency: str) -> bool:
    return (
        quote.get("currency") == currency
        or _EXCHANGE_CURRENCY.get(quote.get("exchange", "")) == currency
    )


def search_yf_symbol(
    symbol: str,
    currency_hint: str | None = None,
    timeout: int = 10,
) -> str | None:
    """Find the best Yahoo Finance ticker for *symbol*.

    Strategy (in order):
    1. Search Yahoo Finance by *symbol*; pick first equity result matching *currency_hint*.
    2. If no match, use the company name from the first result to search again with
       *currency_hint* (handles tickers like AMZN.DE → AMZ.DE on Xetra).
    3. Fall back to the first equity result regardless of currency.

    Returns the Yahoo Finance ticker, or None if nothing found.
    """
    equities = _yahoo_search(symbol, timeout)

    if currency_hint:
        preferred = [q for q in equities if _matches_currency(q, currency_hint)]
        if preferred:
            chosen = preferred[0]["symbol"]
            logger.info("yahoo search: %r + %s → %s", symbol, currency_hint, chosen)
            return chosen

        # No direct match — retry by simplified company name to catch aliased tickers
        # e.g. "AMZN" search misses AMZ.DE; searching "Amazon" (from "Amazon.com, Inc.") finds it
        if equities:
            name = equities[0].get("longname") or equities[0].get("shortname")
            if name:
                simplified = re.split(r"[^a-zA-Z0-9]", name)[0]
                if simplified and simplified != symbol:
                    name_equities = _yahoo_search(simplified, timeout)
                    preferred_by_name = [q for q in name_equities if _matches_currency(q, currency_hint)]
                    if preferred_by_name:
                        chosen = preferred_by_name[0]["symbol"]
                        logger.info("yahoo search: %r + %s → %s (via %r)", symbol, currency_hint, chosen, simplified)
                        return chosen

    if equities:
        chosen = equities[0]["symbol"]
        logger.info("yahoo search: %r (no currency match) → %s", symbol, chosen)
        return chosen

    logger.warning("yahoo search: no equity results for %r", symbol)
    return None

# XTB suffix → Yahoo Finance suffix (empty string = no suffix for US).
# TODO: .UK needs per-instrument verification — some XTB .UK tickers are USD (R2US.UK, BCHN.UK).
_XTB_TO_YF: dict[str, str] = {
    "US": "",     # AAPL.US → AAPL
    "PL": ".WA",  # PKN.PL  → PKN.WA  (Warsaw)
    "NL": ".AS",  # PRX.NL  → PRX.AS  (Euronext Amsterdam)
    "DE": ".DE",  # SXR8.DE → SXR8.DE (Xetra)
    "UK": ".L",   # R2US.UK → R2US.L  (London) — verify per instrument
}

# XTB exchange suffix → ISO currency, used to drive the search fallback.
_XTB_SUFFIX_CCY: dict[str, str] = {
    "US": "USD",
    "DE": "EUR",
    "NL": "EUR",
    "PL": "PLN",
    "UK": "GBP",
}


def load_ticker_map(path: Path) -> dict[str, str]:
    """Load XTB-symbol → Yahoo-ticker mappings from a YAML file."""
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return {str(k): str(v) for k, v in (data or {}).items()}


def save_ticker_map(path: Path, ticker_map: dict[str, str]) -> None:
    """Persist ticker mappings to YAML, sorted for stable diffs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(dict(sorted(ticker_map.items())), f,
                  default_flow_style=False, sort_keys=False, allow_unicode=True)


def _to_yf(symbol: str) -> str:
    """Convert XTB ticker to Yahoo Finance ticker."""
    if "." not in symbol:
        return symbol
    base, suffix = symbol.rsplit(".", 1)
    yf_suffix = _XTB_TO_YF.get(suffix.upper(), f".{suffix}")
    return f"{base}{yf_suffix}"


class YahooFinanceProvider:
    """Fetch EOD prices from Yahoo Finance v8 chart API (no API key required).

    Returns Quote.currency from the API response directly — no suffix heuristics needed.
    Note: Yahoo's undocumented API; may return HTTP 429 under load. Use via CachingProvider.

    ticker_map_path: if given, auto-resolved mappings are persisted there so future runs
    skip the search and users can inspect / correct them manually.
    """

    def __init__(self, timeout: int = 10, ticker_map_path: Path | None = None) -> None:
        self._timeout = timeout
        self._ticker_map_path = ticker_map_path
        self._ticker_map: dict[str, str] = load_ticker_map(ticker_map_path) if ticker_map_path else {}

    def latest(self, symbols: list[str]) -> dict[str, Quote]:
        result: dict[str, Quote] = {}
        for symbol in symbols:
            try:
                q = self._fetch(symbol)
                if q is not None:
                    result[symbol] = q
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    logger.warning("yahoo: symbol not found: %s", symbol)
                else:
                    logger.warning("yahoo: HTTP error for %s: %s", symbol, exc)
            except Exception:
                logger.warning("yahoo: failed to fetch %s", symbol, exc_info=True)
        return result

    def _search_fallback(self, symbol: str) -> str | None:
        """Resolve a failed symbol via Yahoo search, then persist the result."""
        base = symbol.rsplit(".", 1)[0] if "." in symbol else symbol
        suffix = symbol.rsplit(".", 1)[1].upper() if "." in symbol else ""
        ccy = _XTB_SUFFIX_CCY.get(suffix)
        resolved = search_yf_symbol(base, currency_hint=ccy, timeout=self._timeout)
        if resolved:
            logger.info("yahoo: %s resolved via search → %s", symbol, resolved)
            self._ticker_map[symbol] = resolved
            if self._ticker_map_path:
                save_ticker_map(self._ticker_map_path, self._ticker_map)
        return resolved

    def _fetch(self, symbol: str) -> Quote | None:
        yf_ticker = self._ticker_map.get(symbol) or _to_yf(symbol)
        resp = requests.get(
            f"{_BASE}/{yf_ticker}",
            params={"interval": "1d", "range": "1d"},
            headers=_HEADERS,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("chart", {}).get("result") or []
        meta = results[0]["meta"] if results else {}
        price_val = meta.get("regularMarketPrice")
        # Trigger search fallback when Yahoo has no usable data (empty result or null currency/price)
        if not results or (price_val is None and meta.get("currency") is None):
            resolved = self._search_fallback(symbol)
            if resolved is None:
                hint = (
                    f" — add '{symbol}: <yahoo-ticker>' to {self._ticker_map_path}"
                    if self._ticker_map_path
                    else ""
                )
                logger.warning("yahoo: could not resolve price for %s%s", symbol, hint)
                return None
            return self._fetch(symbol)  # retry — self._ticker_map[symbol] is now set
        if price_val is None:
            logger.warning("yahoo: no price in response for %s", symbol)
            return None
        ts = meta.get("regularMarketTime")
        as_of = datetime.fromtimestamp(ts).date() if ts else date.today()
        return Quote(
            symbol=symbol,
            price=Decimal(str(price_val)),
            currency=meta.get("currency", "USD"),
            as_of=as_of,
            source="yahoo",
        )

    def history(self, symbol: str, start: date, end: date) -> list[Bar]:
        # TODO: fetch OHLCV history from Yahoo Finance chart API
        raise NotImplementedError
