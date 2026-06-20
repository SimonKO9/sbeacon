# Configuration — Implementation Brief

Companion to `ALLOCATION.md`, `SUMMARY.md`, `PNL.md`, `TAX.md`.

A single **`config.yaml`** holds user settings — currently target allocations and instrument
overrides, extensible to other knobs (reporting currency, providers) later. It is the home for
everything that can't be auto-discovered. If it doesn't exist, the app **generates it with sensible
defaults** on first use and tells the user where it wrote it.

---

## File structure

```yaml
# config.yaml — auto-generated if missing
reporting_currency: PLN

# Target allocation by role. RAW weights — they need NOT sum to 1; normalized at read time.
targets:
  core:         0.40
  satellite:    0.25
  thematic:     0.10
  real-assets:  0.10
  crypto:       0.05
  fixed-income: 0.05
  cash:         0.05

# Instrument overrides — OVERRIDES ONLY. Absent tickers use auto-discovery (see ALLOCATION.md).
# Keyed by ISIN where known, ticker otherwise. Sparse: just the corrections.
instruments:
  SOMEFUND.DE: { role: core }                  # auto guessed thematic; it's a broad index
  CDR.PL:      { asset_class: equity }          # confirm/correct asset class
  FOO.PL:      { role: satellite, asset_class: equity }

# Off-platform assets — VALUE ONLY. Valid keys: `real-assets`, `reserves`. Hand-edited.
extra-assets:
  real-assets:               # part of the allocation picture (e.g. property)
    - name: Apartment in Poznań
      value: 700000
      currency: PLN          # defaults to reporting_currency if omitted
  reserves:                  # ringfenced cash — net-worth only, OUTSIDE the allocation
    - name: Emergency fund
      value: 50000
      currency: PLN
```

**Targets normalize, never validated to sum to 1.** Set any raw weights you like; the allocation
lens divides each by the total. So `core: 0.5, satellite: 0.25` with nothing else → 66.7% / 33.3%.
`set-allocation` therefore just writes one number — it never has to rebalance the others.

**`instruments` is overrides-only.** Auto-discovery assigns every held instrument; this key holds
only the ones you've corrected. Re-imports never touch it.

**`extra-assets` are value-only.** Off-platform holdings with `name`, `value`, and optional
`currency` (default reporting). They have **no cost basis and no cashflows**, so by construction they
never touch P/L, realized/unrealized, or XIRR. Two valid keys, treated differently:

- **`real-assets`** (e.g. property) — *part of the allocation picture*. Maps to role `real-assets`
  and asset-class `real-estate` automatically (no per-entry tags), so it shows in both lenses and in
  `summary`'s net-worth total. It enters the allocation denominator, so adding one correctly lowers
  every liquid holding's weight.
- **`reserves`** (ringfenced emergency cash) — *net-worth only*. Counts toward `summary`'s net-worth
  total and shows as its own memo line, but is **fully outside the allocation lens**: no bucket, no
  weight, no target, no drift, no rebalance, and **not in the denominator**. Critically, it is
  **never merged into the investable `cash` bucket** — that would inflate your cash weight and make
  the rebalancer try to deploy money you've deliberately set aside.

Static figures; edit the YAML when a value changes (no valuation dates, no liabilities — register
net equity if an asset is financed).

---

## `config` command group

```
config path                                              # show config.yaml location
config get                                               # print the whole config (defaults + edits)
config get-allocations                                   # targets: raw weight + normalized %
config set-allocation <role> <weight>                    # e.g. set-allocation core 0.5
config set-instrument-role <ticker> <role>               # e.g. set-instrument-role FOO.PL satellite
config set-instrument-asset-class <ticker> <asset-class> # e.g. set-instrument-asset-class CDR.PL equity
config clear-instrument <ticker>                         # remove a ticker's overrides → revert to auto
```

`get-allocations` shows both columns so the normalization is visible:

```
Role           Raw     Target %
core           0.40     36.4%
satellite      0.25     22.7%
thematic       0.10      9.1%
real-assets    0.10      9.1%
crypto         0.05      4.5%
fixed-income   0.05      4.5%
cash           0.05      4.5%
──────────────────────────────
               1.10    100.0%
```

(Raw column needn't be 1.10 or anything in particular — it's just the sum being normalized away.)

---

## Vocabularies & validation

- **Roles:** `core`, `satellite`, `thematic`, `real-assets`, `crypto`, `fixed-income`, `cash`.
- **Asset classes:** `equity`, `fixed-income`, `commodity`, `crypto`, `cash`, `real-estate`. Alias: `stock` → `equity`.
- **`extra-assets` keys:** only `real-assets` and `reserves` — reject any other key.
- Setters **validate** against these and reject unknown values (so a typo errors instead of silently
  creating a junk bucket). `set-allocation` accepts any non-negative number.
- `set-*` create the `targets` / `instruments` key if absent. `clear-instrument` removes the ticker's
  entry; if it had only one overridden field, that field reverts to auto.

---

## Behavior

- **Auto-generate:** on any run needing config, if `config.yaml` is missing, write the defaults above
  and print `generated config.yaml at <path>`. Never silently proceed without one.
- **Edits are surgical:** `config set-*` rewrites only the touched key, preserving comments/order
  where practical (or document that comments aren't preserved if using a plain YAML dump).
- **Resolution order** for an instrument's tags: `config.instruments` override → auto-discovery →
  `untagged`. The resolved result is what `instruments list` shows, with its source.
