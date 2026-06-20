# Price Data Provider — Implementation Brief

Companion to `DESIGN.md`. Scope: the **pricing layer** that fetches current prices for held
instruments and values them in PLN. Holdings span GPW (`.PL`), Xetra (`.DE`), US (`.US`),
LSE (`.UK`), plus ETCs/ETNs. EOD / delayed quotes are sufficient (this is a tracker, not a
trading engine). Everything sits behind a `PriceProvider` interface and is cached.

> Rate-limit / free-tier figures below are current as of mid-2026 and **should be re-verified**
> before relying on them — they change often.

---

## Decision

- **Primary: stooq.** The only free source that covers GPW *and* also does Xetra/US/LSE, so one
  provider covers every venue held. No API key. Its ticker suffixes match XTB's (lowercased).
- **Fallback: yfinance.** Used only when a stooq symbol won't resolve, or as a cross-check.
- **Optional keyed API** (Twelve Data / Finnhub) only if a contract/SLA feel is wanted — they're
  US/large-cap-strong and weak on GPW. Skip Alpha Vantage and Polygon.

The hard part is **not** the fetch layer — it's mapping XTB's partly-proprietary tickers to
provider symbols via ISIN (see "Ticker / ISIN resolution"). Budget effort there.

---

## stooq (primary)

- **Coverage:** PL, DE, US, UK equities/ETFs/ETCs, plus FX pairs, indices, commodities.
- **Ticker format:** lowercase `{symbol}.{suffix}` — `aapl.us`, `pkn.pl`, `vvsm.de`, `r2us.uk`.
  Suffix = XTB suffix lowercased. **Base symbol may differ** for XTB-proprietary tickers — resolve
  via ISIN, don't assume the XTB symbol is the stooq symbol.
- **Access (no key):**
  - `pandas-datareader`:
    ```python
    import pandas_datareader.data as web
    df = web.DataReader("aapl.us", "stooq")            # OHLCV DataFrame, newest-first
    # batch: pandas_datareader.stooq.StooqDailyReader(symbols=[...], chunksize=25)
    ```
  - Direct CSV:
    - Latest quote: `https://stooq.com/q/l/?s={ticker}&f=sd2t2ohlcv&h&e=csv`
    - Daily history: `https://stooq.com/q/d/l/?s={ticker}&i=d`
- **Data semantics:** close is **split/dividend-adjusted**; EOD or ~15-min delayed. Use for
  *current value only* — cost basis stays from the ledger (don't mix adjusted price into basis).
- **Limits:** a daily download cap, and a CAPTCHA appears if hammered. Both are avoided entirely by
  caching + modest volume (see Caching). No auth, no key.
- **FX:** same endpoints serve pairs — `eurpln`, `usdpln`, `gbppln`.

## yfinance (fallback)

- Free, pandas-native, strong on US. **It's a Yahoo scraper:** undocumented limits (~a few hundred
  requests/day per IP by community estimate), returns HTTP 429 and can temporarily blacklist an IP
  under load.
- Use as a **cached fallback** only. The `yfinance-cache` wrapper (ValueRaider) adds caching.
- **Different ticker convention:** `.WA` Warsaw, `.L` London, `.DE` Xetra, bare symbol for US — so
  it needs its own symbol mapping, separate from stooq's.

## Keyed APIs (optional; mostly US, weak GPW)

| Provider | Free tier | Notes |
|---|---|---|
| Finnhub | 60 calls/min | US real-time-ish (≈20-min delay free), WebSocket (limited symbols) |
| Twelve Data | 800 calls/day | Widest global coverage of the three; *may* carry Warsaw — verify per ticker |
| Alpha Vantage | 25 req/day, 5/min | Too stingy for a multi-ticker daily tracker — skip |
| Polygon | none | No free tier — skip |

None beats stooq for GPW names.

---

## Ticker / ISIN resolution (the real work)

- XTB tickers are partly proprietary: `ETCGLDRMAU.PL`, `XXBT.DE`, `ETFBNDXPL.PL`, `ETNVIRBTCP.PL`,
  `LYPS.PL` etc. are not universal exchange symbols.
- Keep an **instrument master keyed by ISIN**. Per provider, store the provider's symbol. Resolution
  path: `XTB ticker → ISIN → provider symbol`.
- For plain listings the lowercase-suffix heuristic works (`RDDT.US → rddt.us`); for ETPs and
  XTB-internal tickers, fall back to the ISIN mapping table (seed manually, extend as needed).
- **Currency per instrument also comes from the master, not the suffix** — `.UK` lines can be USD
  (verified for `R2US.UK`/`BCHN.UK`). This is the same instrument-master concern from `DESIGN.md` §8.

---

## Caching & throttling

- **Granularity:** EOD daily close is enough. Cache keyed by `(provider, symbol, date)`.
- **Volume:** one fetch per held symbol per day → tens of requests total → no provider's limit is a
  concern. Caching is what makes "won't throttle me" true across all options.
- **Mechanism:** persistent cache (a DuckDB table or parquet alongside the ledger) + a staleness
  window (e.g. accept same-trading-day quotes). On a cache hit within the window, no network call.
- **Resilience:** exponential backoff on HTTP 429; on miss/error from the primary, fall through to
  the fallback provider. Never loop-hammer stooq (CAPTCHA risk).

## FX to PLN

- Market valuation: stooq pairs (`eurpln`, `usdpln`, `gbppln`).
- Tax/realized cost basis: **NBP API** for official D-1 rates (already specified in `DESIGN.md` §5).
- Keep the two distinct — NBP for anything tax-relevant, market FX for live valuation.

---

## Suggested interface

```python
from typing import Protocol
from datetime import date

class PriceProvider(Protocol):
    def latest(self, symbols: list[str]) -> dict[str, "Quote"]: ...
    def history(self, symbol: str, start: date, end: date) -> list["Bar"]: ...

# Quote: symbol, price (Decimal), currency, as_of, source
# Bar:   date, open, high, low, close (Decimal), volume
```

- `StooqProvider` (primary) and `YFinanceProvider` (fallback) implement it.
- A `CachingProvider` decorator wraps any provider; a `CompositeProvider` tries primary then fallback.
- All monetary values `Decimal`; symbols resolved via the ISIN-keyed instrument master before the call.

## Gotchas checklist

- Lowercase tickers for stooq; map proprietary XTB tickers via ISIN.
- Don't infer currency from the suffix — read it from the instrument master.
- stooq close is adjusted — keep ledger cost basis separate.
- Cache everything; back off on 429; fall through providers on failure.
- Re-verify all rate-limit / free-tier figures before depending on them.
