from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

import requests

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


def search_yf_symbol(
    symbol: str,
    currency_hint: str | None = None,
    timeout: int = 10,
) -> str | None:
    """Find the best Yahoo Finance ticker for *symbol*.

    Strategy (in order):
    1. Search Yahoo Finance; pick first equity result matching *currency_hint*.
    2. If no match and *currency_hint* given, probe common exchange suffixes
       (e.g. .DE, .AS) and return the first that resolves with the right currency.
    3. Fall back to the first equity result regardless of currency.

    Returns the Yahoo Finance ticker, or None if nothing found.
    """
    try:
        resp = requests.get(
            _SEARCH_URL,
            params={"q": symbol, "quotesCount": 10, "newsCount": 0},
            headers=_HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception:
        logger.warning("yahoo search: request failed for %r", symbol, exc_info=True)
        return None

    quotes = resp.json().get("quotes") or []
    equities = [q for q in quotes if q.get("quoteType") == "EQUITY"]

    if currency_hint:
        preferred = [
            q for q in equities
            if (
                q.get("currency") == currency_hint
                or _EXCHANGE_CURRENCY.get(q.get("exchange", "")) == currency_hint
            )
        ]
        if preferred:
            chosen = preferred[0]["symbol"]
            logger.info("yahoo search: %r + %s → %s", symbol, currency_hint, chosen)
            return chosen

        # Search found no currency match — try probing known suffixes
        probed = _probe_suffix(symbol, currency_hint, timeout=timeout)
        if probed:
            return probed

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

# XTB-proprietary tickers that don't resolve via suffix rules.
# TODO: replace with ISIN-based resolution (PRICING.md).
_OVERRIDES: dict[str, str] = {
    "LYPS.PL": "LYPS.DE",  # Amundi Core S&P 500 UCITS ETF — XTB Warsaw ticker, resolves on Xetra
}


def _to_yf(symbol: str) -> str:
    """Convert XTB ticker to Yahoo Finance ticker."""
    if symbol in _OVERRIDES:
        return _OVERRIDES[symbol]
    if "." not in symbol:
        return symbol
    base, suffix = symbol.rsplit(".", 1)
    yf_suffix = _XTB_TO_YF.get(suffix.upper(), f".{suffix}")
    return f"{base}{yf_suffix}"


class YahooFinanceProvider:
    """Fetch EOD prices from Yahoo Finance v8 chart API (no API key required).

    Returns Quote.currency from the API response directly — no suffix heuristics needed.
    Note: Yahoo's undocumented API; may return HTTP 429 under load. Use via CachingProvider.
    """

    def __init__(self, timeout: int = 10) -> None:
        self._timeout = timeout

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

    def _fetch(self, symbol: str) -> Quote | None:
        yf_ticker = _to_yf(symbol)
        resp = requests.get(
            f"{_BASE}/{yf_ticker}",
            params={"interval": "1d", "range": "1d"},
            headers=_HEADERS,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("chart", {}).get("result") or []
        if not results:
            logger.warning("yahoo: no data for %s (YF ticker: %s)", symbol, yf_ticker)
            return None
        meta = results[0]["meta"]
        price_val = meta.get("regularMarketPrice")
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
