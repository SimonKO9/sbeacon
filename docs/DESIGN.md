# Portfolio Tracker — High-Level Design

**Status:** draft / design phase. Implementation details deferred.
**Scope of v1:** ingest XTB stock exports, derive open positions and long-term P/L (per account and globally), CLI frontend.

---

## 1. Goals and non-goals

**Goals**
- Separate *data ingestion* from *representation*: external sources are converted into one internal format; everything downstream reads only that format.
- Track multiple accounts separately and aggregate across them.
- Handle currencies correctly (native vs. reporting currency, FX at trade time and now).
- Answer two core questions: how are my open positions doing, and what is my long-term realized + unrealized P/L per account and globally.
- Be re-runnable: re-importing overlapping exports must not double-count.

**Non-goals for v1 (but must not be designed out)**
- Asset classes other than equities (crypto, CFDs, bonds, FX) — extensible, not implemented.
- Lot-matching policies other than FIFO — pluggable, only FIFO implemented.
- Tax report generation — the data model must support it; the report itself is later.
- Web UI / API — the core must be frontend-agnostic; only the CLI is built now.

---

## 2. Key design decisions

1. **Event ledger, not a state file.** Broker exports are transaction logs. The internal representation is an append-only ledger of normalized events. All views (positions, cash, P/L, allocation) are *derived* by folding over events. Re-imports, corrections, and corporate actions (e.g. stock splits) fall out naturally instead of corrupting hand-maintained state.

2. **Frontend-agnostic core.** The reports/analytics layer is pure functions: `events → result`, with no I/O and no presentation. The CLI is one caller. A future web API or UI is another caller of the exact same functions. This is what makes "add a web UI later" cheap — it's already true.

3. **Adapters isolate all source quirks.** One adapter per source. Each does file discovery + parse + normalize. No broker-specific logic exists anywhere outside its adapter.

4. **Stable event identity.** Every event has an `id` used for idempotent imports (insert-on-conflict-ignore). Prefer the broker's transaction id; fall back to a content hash of the row only if none exists. *(Open: confirm XTB exports a txn id — see §10.)*

5. **Decimal everywhere.** Money and quantities are `Decimal`, never float.

6. **Auditability.** Every event keeps a `SourceRef` back to the originating file and raw row, so any reported number can be traced to its export line.

---

## 3. Architecture

```
                      ┌─────────────┐
   CLI  ────────────► │             │
   (future: web API)  │    Core     │
                      │  Reports/   │ ◄── pure functions over the ledger
   Adapters ────────► │  Analytics  │
   (XTB, future:      │             │
    IBKR, exchanges)  └──────┬──────┘
        │                    │
        ▼                    │
   Domain (Event,            │
   Instrument, Account)      │
        │                    │
        ▼                    ▼
   Storage (ledger) ◄── Pricing + FX providers
```

| Layer | Responsibility | Notes |
|---|---|---|
| Frontend | Parse user intent, format output | CLI now (Typer or argparse). Web is a future sibling. |
| Adapters | Discover + parse + normalize one source | All quirks live here. |
| Domain | Event / Instrument / Account types | No I/O. |
| Storage | Persist the ledger, enforce idempotency | Canonical ledger + rebuildable query index. |
| Reports | Derive positions, P/L, allocation, performance | Pure functions; shared by all frontends. |
| Pricing / FX | Current quotes and exchange rates | Pluggable providers. |

---

## 4. Internal representation

### 4.1 Event

A single discriminated type. Sketch (shape, not implementation):

```python
class EventType(Enum):
    TRADE = "trade"            # buy/sell
    DIVIDEND = "dividend"
    FEE = "fee"                # standalone commission/charge
    INTEREST = "interest"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"  # taxable event for IKE/IKZE — see §6
    FX_CONVERSION = "fx"
    TAX = "tax"
    CORPORATE_ACTION = "corp"  # split, ticker change, etc.

@dataclass(frozen=True)
class Event:
    id: str                      # stable: broker txn id, else content hash
    account_id: str
    timestamp: datetime
    type: EventType
    instrument: Instrument | None  # None for pure cash ops
    quantity: Decimal | None       # signed: + buy, - sell
    price: Decimal | None          # in quote currency
    amount: Decimal                # cash impact, in `currency`
    currency: str                  # native/quote currency (USD, PLN, EUR…)
    fees: Decimal = Decimal(0)
    source: SourceRef              # file + raw row reference
```

### 4.2 Instrument

Carries the identity problem (broker ticker ≠ Yahoo ticker ≠ ISIN):

- `symbol`, `name`, `asset_class`, `quote_currency`, `isin`, `exchange`.
- `asset_class` is present from day one (= `EQUITY` in v1) so other classes slot in without a schema change. ISIN is the most stable key for resolving market-data quotes.

### 4.3 Account

First-class, tracked separately, aggregated in reports:

- `account_id`, `broker`, `type`, `base_currency`.
- `type` ∈ { `REGULAR`, `IKE`, `IKZE`, … } — drives tax treatment (§6). Extensible to `CFD`, `CRYPTO` later.

---

## 5. Currency model

Three distinct concepts, kept separate:

- **Native currency** — the currency an event actually happened in (quote currency of the instrument). Stored untouched on the event.
- **Reporting currency** — a single currency all views convert to. Default **PLN**, overridable per command.
- **FX provider** — converts native → reporting at two moments: *at event time* (for realized figures and tax cost basis) and *now* (for open-position market value).

Realized P/L in native USD and in PLN differ by FX movement; that FX gain/loss is part of the actual cost basis for taxable accounts. Default FX source: **NBP** (free, official, matches the Polish D-1 tax convention). The provider is an interface, so other sources can be swapped in. Note that XTB already embeds the conversion rate it used on each closed trade (open and close), so for ingested XTB trades we record those directly; NBP is the fallback for events without an embedded rate (e.g. open-position valuation, cash ops).

---

## 6. Account and tax model

Tax treatment is a function of the account's **wrapper** (`REGULAR` / `IKE` / `IKZE`). v1 captures the data needed; report generation is later.

- **REGULAR** — realized gains taxable; FIFO cost basis (§7), FX at trade time (NBP D-1). Interest and dividends are taxed too (the regular export shows `Free funds interest tax` ≈ 19% of the interest; IKE shows none).
- **IKE / IKZE** — sells inside the wrapper are **not** taxable; the taxable event is a **withdrawal**. Interest/dividends arrive gross. So the model must (a) not treat IKE/IKZE realized sells as taxable, and (b) retain withdrawal events as the trigger.

**Wrapper is not reliably derivable from the export.** The XTB `Product` column is a *sub-account / strategy* label, not the wrapper: in an IKE export every row reads `IKE`, but the regular export shows `My Trades` and `Investment Plans` under the *same* header account number. And that header number is stamped identically across different account exports (§8). So the wrapper (and the account boundary) is **declared per file/account by the user**, not inferred; `Product` is captured as a sub-account dimension within an account. Because tax keys off the declared wrapper and the ledger records realized lots, FX-at-trade, withdrawals, and per-type tax rows, a tax view can be added later without remodeling.

The PLN, EUR, and USD accounts form **one `REGULAR` taxable pool**; IKE and IKZE are separate boxes. This matters for lot matching: when the same instrument is held across two currency accounts, the FIFO/tax scope is the pool, not the single account, so matching scope (`per-account` vs `per-wrapper-pool`) is a configurable parameter (§7), defaulting to the pool for taxable accounts.

---

## 7. Lot matching

- Realized P/L requires matching sells against prior buys.
- **FIFO** is the v1 policy and the only one implemented.
- It sits behind a `LotMatchingPolicy` interface so LIFO / average-cost / specific-lot / pooled-aggregate can be added later by config, without touching the ledger or reports. (A pooled-aggregate-with-carryforward policy is what a future crypto box would need — structurally unlike per-lot FIFO, which is why the interface exists rather than a hardcoded FIFO.)

Realized P/L is computed by **our own FIFO** over the raw buy/sell stream in the Cash Operations ledger (§8). Total realized P/L over a fully-closed holding is matching-method-independent (Σproceeds − Σcosts); matching only affects per-year attribution of partially-closed positions, where FIFO is what we want (the PL tax rule) and IKE/IKZE is untaxed anyway. XTB's Closed Positions tab is ignored (§8), so there is no automatic cross-check on FIFO output — acceptable given FIFO is deterministic from the ledger.

---

## 8. Ingestion — XTB adapter (first concrete source)

Command: `tracker load xtb --paths=./data/xtb`

**File layout (confirmed against real exports).** One `.xlsx` per account. Each workbook's header rows carry an account id, the sheet name, and a date range. **Cash Operations is the sole source** for v1:
- **Cash Operations** — the raw transaction ledger: one row per buy, sell, dividend, interest, fee, tax, deposit, and transfer. Everything is derived from this.
- **Closed Positions** — XTB's pre-matched realized view. **Ignored.** It contains nothing not reconstructible from Cash Operations (the open/close pairing is just FIFO, which we run ourselves; its conversion rates equal the implied `|Amount|/(qty×price)`). Its only value would be a redundant cross-check on our FIFO, which we forgo for now. Optional future reconciliation input.
- **Open Positions** — *not produced by these exports.* Current holdings are derived by folding Cash Operations (Σ buys − Σ sells, FIFO) per `(account, instrument)`.

Two consequences of relying on Cash Operations alone:
- **Exports must be full history.** Holdings/cost basis are rebuilt from zero, so a partial-window export yields wrong opening positions. Samples run `2006-01-01 → now`; `--dry-run` should warn if `Date from` isn't the account's inception.
- **Corporate actions are the residual risk.** Splits / ticker changes alter share counts with no transaction row, and there is no Open-Positions anchor to catch the drift (relevant: NOW's 5:1 split). v1 mitigations: a `CORPORATE_ACTION` event path (manually entered or future-sourced) and an occasional manual quantity check against the XTB app. Cash *is* anchored — the trailing `Total` row gives a free-cash figure to reconcile Σ(Amount) against.

**Cash Operations — column semantics (confirmed across IKE + regular exports):**
- `Amount` is the net cash impact in the **account's base currency**, FX already applied (− out, + in). Base currency is per account, declared per file (see account map below) — PLN/IKE/IKZE files are in PLN, the EUR and USD account files are in EUR/USD. Implied per-trade FX = `|Amount| / (qty × native price)`, which is ≈1 when the instrument currency equals the account base.
- `ID` is row-unique per operation → event `id` = `{account}:{ID}`. No content hashing needed.
- **`Type` → event:** `Stock purchase`→buy, `Stock sell`→sell, `Dividend` / `Dividend from foreign company on PL market`→dividend, `Free funds interest`→interest, `Free funds interest tax`→tax, `Withholding tax`→tax (dividend withholding; negative, carries ticker), `SEC fee`→fee, `Deposit`→external cash in, `Transfer`→currency conversion (internal), `Subaccount transfer`→internal move, `IKE deposit`/`IKZE deposit`→wrapper funding (internal; sign depends on the side). Still unseen: an explicit bank **withdrawal**.
- **Tax events carry a category** — `interest_tax` (final) vs `dividend_withholding` (may be foreign and creditable). `dividend_withholding` pairs to its dividend by `(account, ticker, timestamp)` so the `dividends` report can show gross / withheld / net. Foreign withholding (e.g. US 15%) is taken at source even inside IKE/IKZE, so tax rows can appear in any wrapper.
- **External vs internal cash — critical for `flows`/`performance`.** Only `Deposit` rows from bank rails (`Adyen BLIK`, `Blik(Payu)`, `Pekao S.A.`, `BlueCash`) are contributed capital. Internal movements net to zero and come in two scopes: *intra-file* — `Subaccount transfer` signed pairs at one timestamp (My Trades ↔ Investment Plans inside PLN); *inter-file* — currency conversions (`Transfer`, `PLN to USD … to: 50972260`) and `IKE/IKZE deposit` funding, which appear as an out-leg in one account file and an in-leg in another. Inter-file legs are paired across files by the TA ids in the comment + matching timestamp/amount and netted, so a PLN→IKE or PLN→USD move is never counted as new capital. (This is the cash side of the §12 transfer concept — needed in v1.)
- **Quantity and native price are only in the free-text `Comment`** (`OPEN BUY 3 @ 102.95`, `CLOSE BUY 47/304 @ 10.8400`). The comment is a **per-`Type` dispatcher**, not one regex: trades parse `(OPEN|CLOSE) BUY {qty}[/{total}] @ {price}` (first number = this row's qty; qty fractional to 4 dp); `Transfer` parses `... {CCY1} to {CCY2} ... Exchange rate:{r}`; `Subaccount transfer` parses `Transfer from {id} to {id}`; deposits classify by provider keyword. Correction rows exist (`Corr …`). Native FX recoverable as `|Amount| / (qty × price)`.
- **Mixed decimal separators within a row:** `Amount` uses comma (`-1145,4`), comment price uses dot (`@ 102.95`). Per-field convention.
- A trailing **`Total` row** (free-cash balance) must be skipped, not parsed as an event.
- Dividends are paid **per lot** (many rows, same timestamp), gross inside IKE. Foreign dividends carry native currency/rate in the comment (`EUR 0.0960/ SHR`).
- **Currency per instrument needs resolution, not a suffix rule.** `.US`→USD, `.DE`→EUR, `.PL`→PLN hold, but `.UK` lines can be **USD** (verified: `R2US.UK`/`BCHN.UK` imply ≈3.88 PLN, i.e. USD/PLN, not GBP). Resolve currency per instrument (ISIN/market data), using the implied `|Amount|/(qty×price)` rate as a cross-check; suffix is only a hint. ETCs/ETNs (physical-gold, bitcoin/blockchain trackers) are securities, fully in scope — not routed to external crypto tooling.

**Pipeline:**
1. **Discover** — walk `--paths` recursively, collect every `.xlsx`. Account identity is **not** read from the file (the header number is a client master id, identical across accounts); it comes from the **filename prefix** (token before the first `_`), matched against a built-in convention table — no per-file config:

   | Prefix | Wrapper | Base ccy |
   |---|---|---|
   | `PLN_` | REGULAR | PLN |
   | `EUR_` | REGULAR | EUR |
   | `USD_` | REGULAR | USD |
   | `IKE_` | IKE | PLN |
   | `IKZE_` | IKZE | PLN |

   The prefix yields the wrapper; base currency is a separate column (note `IKE_`/`IKZE_` are PLN-based, not "their own currency"). The three REGULAR files share one tax pool (§6). The filename suffix is free, so dated re-exports under the same prefix merge into the same account (idempotent dedup by `{account}:{ID}`). An unmatched prefix is **skipped with a warning** (never guessed); an optional `--account` override covers renamed files or future brokers.
2. **Parse** — locate each tab's header row; parse fields with per-field locale → `Decimal`; parse trade comments for qty/native price.
3. **Normalize** — Cash Operations rows → events (`id = {account}:{ID}`, `SourceRef` attached). Holdings/realized P/L are derived downstream by FIFO; no other tab is read.
4. **Idempotent write** — insert-on-conflict-ignore by `id`.
5. **Reconcile** — derived free cash (Σ amounts) checked against the trailing `Total` row; flag unparsed rows, unrecognized `Type` strings, and any negative derived position (a likely sign of a missed corporate action or partial-history export). Holdings have no broker-supplied anchor, so a periodic manual check against the XTB app is the backstop.

Support `--dry-run`: print what *would* import (counts per type, date range, unparsed rows, unrecognized `Type` strings, unknown ticker suffixes, comments that fail to parse) so each new export can be trusted before it's committed.

---

## 9. Storage

- **Canonical ledger:** append-only plaintext (JSONL or Parquet), git-versionable — diffable history and audit trail.
- **Query index:** SQLite (or DuckDB) rebuilt from the canonical ledger, with a unique constraint on `id` for dedup and real query support across accounts / years / asset classes.
- Git is the audit layer, not the query engine.

---

## 10. Commands

**Ingest**
- `load xtb --paths=… [--account=…] [--dry-run]`
- `accounts` / `sources` — what's loaded.

**Inspect**
- `positions [--account] [--asset-class] [--reporting-ccy=PLN]` — open positions: qty, avg cost (native + reporting), market value, unrealized P/L, weight %.
- `position <symbol>` — lots, realized + unrealized, full event history.
- `cash` — balances per account per currency.

**P/L and income**
- `pnl [--period=YYYY | --from --to] [--by=account|instrument|asset-class|currency] [--realized|--unrealized]`
- `dividends [--year]` — gross / withholding / net.
- `fees` — total drag: commission + tax.

**Big picture**
- `summary` — total value, net invested, total P/L, broken out per account and globally.
- `allocation [--by=asset-class|currency|account]` — exposure vs. target.
- `flows` — net deposits/withdrawals (contributed capital).
- `performance` — see §11.

**Maintenance**
- `prices refresh` / `fx refresh`
- `reconcile` — does derived cash match broker-reported cash? Flags gaps and unparsed rows.
- `export --format=xlsx`

---

## 11. Performance measurement

"How am I doing" has three answers that diverge with staggered deposits:

- **Absolute P/L** — value − net contributions.
- **Money-weighted (XIRR)** — return over actual cashflows + current value. Best fit for irregular contributions.
- **Time-weighted (TWR)** — strips deposit timing; comparable to benchmarks.

`performance` defaults to **XIRR + absolute P/L**, with TWR behind a flag. Any single "return %" must state which method it is.

---

## 12. Extensibility points (designed in, not built)

- **New asset classes** — `asset_class` lives on every instrument and `currency: str` already holds any asset identifier, so no ledger reshape is needed. Crypto specifically is **out of scope** (tracked in Koinly), but two seams are left open so a future adapter slots in cleanly:
  - *Asset-as-currency* — the quote leg of a trade can be another asset (e.g. crypto pairs), so `currency` is semantically an asset id, not an ISO-4217 fiat code. A future asset registry (fiat = an asset that has an NBP rate) drops in without changing the event shape.
  - *Asset transfers* — moving an *asset* (not cash) between your own accounts/venues realizes no P/L but is an outflow on one export and an inflow on another. This needs a `TRANSFER` event type plus send/receive reconciliation. **Cash** internal transfers (currency conversions, subaccount transfers, wrapper funding) already occur in XTB stock exports and are handled in v1 (§8); the *asset* case is the remaining future seam, and the `EventType` enum is expected to grow there.
- **New sources** — implement the adapter interface (discover/parse/normalize). Core is untouched.
- **New frontends** — web API/UI calls the same pure report functions as the CLI.
- **New lot policies** — implement `LotMatchingPolicy`; select by config.
- **New FX / price providers** — implement the provider interface.

---

## 13. Open questions

Resolved by the sample exports:
- ~~Transaction id?~~ — Cash Operations has a row-unique `ID`; event `id` = `{account}:{ID}` (§8).
- ~~Which tab is authoritative?~~ — Cash Operations is the sole source; Closed Positions is ignored and Open Positions isn't produced by the exports (§8).
- ~~Unconfirmed `Type` strings?~~ — regular export supplied them: `Deposit`, `Transfer`, `Subaccount transfer`, `Free funds interest tax`, `SEC fee`, `IKE/IKZE deposit` (§8); a full import surfaced `Withholding tax` (dividend withholding) too. Tax is wrapper-dependent for PL tax but foreign WHT applies in any wrapper (§6, §8).
- ~~`.UK`→GBP?~~ — no; `.UK` lines can be USD. Currency is resolved per instrument, not by suffix (§8).
- ~~Account identity / structure?~~ — 5 files / 5 accounts (PLN/EUR/USD `REGULAR` pool + IKE + IKZE), declared via the filename→account map (§8).

Still open / residual risks:
- **Corporate actions** — splits/ticker changes have no transaction row and no holdings anchor; mitigated by a `CORPORATE_ACTION` path + periodic manual qty check (§8). NOW's 5:1 split is the live test case.
- **Full-history requirement** — every export must span account inception or opening positions are wrong; `--dry-run` warns on a non-inception `Date from` (§8).
- **EUR/USD account files** — confirm `Amount` is denominated in EUR/USD as assumed (per-event FX model handles it regardless).
- **Bank withdrawal** `Type` string — not yet seen.
