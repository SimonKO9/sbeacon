# `allocation` Command — Implementation Brief

Companion to `DESIGN.md`, `SUMMARY.md`, `PRICING.md`, `TAX.md`.

`allocation` shows portfolio **composition** through a chosen lens: `allocation --by=<dimension>`.
Composition is by current **market value** in reporting currency (PLN), cash included by default
(`--ex-cash` for the invested-only mix). Same `value × weight` machinery for every lens; only the
grouping key changes.

Two lenses are real and supported. A third (currency) is settlement-only and optional; economic
exposure (revenue geography, look-through region/currency) is **not tracked** — it isn't reliably
knowable for single names, so the tool doesn't pretend to.

---

## Taxonomies

**Asset class** — what a holding *is* (objective-ish): `equity`, `fixed-income`, `commodity`,
`crypto`, `cash`.

**Role** — what *job* it does in the plan (strategic):

| Role | What's in it |
|---|---|
| `core` | Broad-market beta — whole-market / major-index ETFs |
| `satellite` | Single-name conviction (individual stocks) — **idiosyncratic** company risk |
| `thematic` | Theme/sector ETFs — a trend bet, diversified within the theme → **sector** risk |
| `real-assets` | Commodities: gold, broad commodity ETCs |
| `crypto` | Crypto / blockchain ETPs (securities, but crypto exposure) |
| `fixed-income` | Bonds, bond ETFs |
| `cash` | Cash balances and cash-like / money-market ETFs |

`satellite` and `thematic` are both active tilts but differ by **risk type**: satellite is a
single-company (idiosyncratic, blow-up) bet you manage tightly; thematic diversifies that away and
concentrates sector-cycle risk. Keep them separate so the active sleeve shows where single-name risk
sits. **Role follows structure** — a single stock that expresses a theme (e.g. a uranium miner) is
`satellite`, not `thematic`. For themes *across* the portfolio, use an optional cross-cutting
`theme` tag (uranium, AI, …) rather than overloading the role.

The two diverge only on equity: equity is one *asset class* but three *roles* (core / satellite /
thematic). The non-equity buckets are ~the same in both.

**`thematic` is its own role**, not folded into `core`. A theme ETF is a concentrated tilt away from
the market — a long-term *satellite*, not diversified beta — and folding it into core hides sector
concentration, defeating the reason for the split. So it stays a separate bucket.

---

## Lens 1 — Role (`allocation --by=role`)

The strategic view. What makes it actionable is **target weights + drift + rebalance**:

```
Allocation by role — 574,662 PLN (incl. cash)

Role             Value    Weight   Target   Drift     Rebalance
core            210,000    36.5%    40%      −3.5pp     +20,200
satellite       180,000    31.3%    25%      +6.3pp     −36,400
thematic         30,000     5.2%    10%      −4.8pp     +27,500
real-assets      55,000     9.6%    10%      −0.4pp      +2,500
crypto           40,000     7.0%     5%      +2.0pp     −11,300
fixed-income          0     0.0%     5%      −5.0pp     +28,700
cash             59,662    10.4%     5%      +5.4pp     −31,000
──────────────────────────────────────────────────────────────
                574,662   100.0%   100%
untagged              0
```

`drift = weight − target`; `rebalance = (target − weight) × total` (PLN to buy/sell to hit target).
Targets come from config. Print the caveat: **rebalancing isn't free** — trimming in a REGULAR
account is a taxable event (hand off to `tax harvest`), while the same move inside IKE/IKZE is free.

## Lens 2 — Asset class (`allocation --by=asset-class`)

The "what kind of risk" view — simpler, no targets needed (though they can be added):

```
Allocation by asset class — 574,662 PLN

Asset class    Value     Weight
equity        420,000     73.1%
commodity      55,000      9.6%
crypto         40,000      7.0%
fixed-income        0      0.0%
cash           59,662     10.4%
──────────────────────────────────────
              574,662    100.0%
untagged            0
```

---

## Assignment: auto-first, manual override

The model is **auto-discovery for everything, manual as a sparse override**. Auto assigns an
asset-class and role to every held instrument; the metadata file holds only the corrections, so you
never hand-tag the whole portfolio.

**Auto-discovered:** instrument identity (ticker, name), quantity, market value. **Cash** → the cash
bucket. **Asset class** from (a) a one-time enrichment read of the un-ingested Closed Positions tab
for `ticker → category` (STOCK/ETC/ETF) where present, plus (b) name-keyword heuristics
("Physical Gold" → commodity, "Bitcoin"/"Blockchain" → crypto, "Bond"/"Treasury" → fixed-income,
"S&P"/"Nasdaq"/"MSCI"/"World" → broad equity). **Role** by rule from asset-class + structure:
single stock → `satellite`, broad-index equity ETF → `core`, other equity ETF → `thematic`,
commodity → `real-assets`, crypto → `crypto`, bond → `fixed-income`, cash → `cash`.

**Where auto is low-confidence (flagged `review`):** broad-vs-sector ETF (`core` vs `thematic`),
commodity-vs-crypto ETC, ETNs, and any held-never-sold name with no category seed. These are exactly
what you scan and fix.

### `instruments` — the resolved view (read)

```
instruments list                 # every held ticker: effective asset-class + role + source
instruments list --review        # only low-confidence guesses + untagged
```

Edits go through the `config` group (overrides live in `config.yaml`; see `CONFIG.md`):
`config set-instrument-role <ticker> <role>`, `config set-instrument-asset-class <ticker> <class>`,
`config clear-instrument <ticker>`. Loop: `instruments list --review` to spot what auto got wrong →
`config set-instrument-*` to fix it.

`list` shows the **effective** value and its **source** — `auto`, `manual`, or `review`/`untagged`:

```
Ticker          Name                   Asset class   Role         Source
LYPS.PL         S&P 500                equity        core         auto
NOW.US          ServiceNow             equity        satellite    auto
ETCGLDRMAU.PL   Physical Gold          commodity     real-assets  auto
XXBT.DE         Galaxy Phys. Bitcoin   crypto        crypto       auto
SOMEFUND.DE     Whatever ETF           equity        thematic?    review     ← broad-or-sector?
NEWNAME.US      —                      —             —            untagged
```

**Targets** live in `config.yaml` under the `targets` key (raw weights, normalized at read — see
`CONFIG.md`); they're the one thing nothing can infer.

---

## Schema

Targets and instrument overrides share one auto-generated `config.yaml` — full structure and the
`config` command group are in **`CONFIG.md`**. In short:

```yaml
# config.yaml (excerpt)
targets:                        # RAW weights, need not sum to 1 — normalized at read
  core: 0.40
  satellite: 0.25
  # …
instruments:                    # OVERRIDES ONLY; absent tickers use auto-discovery
  SOMEFUND.DE: { role: core }            # auto guessed thematic; it's a broad index
  FOO.PL:      { asset_class: fixed-income }
```

---

## Management & edge notes

- Every lens carries an **`untagged`** bucket — a new position shows up unclassified rather than
  silently landing in the wrong slice (and in `instruments list --review`).
- Composition is **market value** based; weights against total-incl-cash by default, `--ex-cash`
  for invested-only.
- Tags persist across re-imports; ledger ingestion never touches the metadata files.
- Rebalance figures are **pre-tax / pre-cost** — they size the move, not its tax consequence (§
  REGULAR → `tax harvest`).
- Off-platform assets (e.g. property) aren't visible here; a manual-assets file (label, value, role,
  asset_class) folded in would make this true net-worth composition rather than just the XTB sleeve.
