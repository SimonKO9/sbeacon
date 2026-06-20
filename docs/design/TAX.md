# `tax` Command — Implementation Brief

Companion to `DESIGN.md` (§6 wrappers, §7 lot matching, §8 events), `PNL.md`, and `SUMMARY.md`.

`tax` is **not** a filtered `pnl` — it's a different computation under Polish PIT-38 rules. Where
`pnl` shows *economic* P/L at transacted FX, `tax` applies the legal rules: REGULAR-only,
realized-only, FIFO, NBP D-1 FX, loss carryforward. Keeping it a separate command stops a filtered
dashboard from masquerading as a filing number.

> Not tax advice. PIT-38 mechanics, foreign-ETF treatment, and dividend crediting have edge cases —
> confirm with an advisor before filing. This command targets a *defensible estimate*, clearly
> labelled as such until NBP rates are wired in and verified.

---

## Tax rules to encode

- **REGULAR accounts only.** PLN + EUR + USD form the taxable pool. **IKE and IKZE are excluded
  entirely** — their sells aren't taxed (they only matter at withdrawal, out of scope here).
- **Realized only.** PIT-38 taxes realized capital gains; unrealized/paper gains are never taxed.
  No mark-to-market.
- **FIFO per instrument, across the REGULAR pool** (the pool, not per-account — §6/§7).
- **FX = NBP average rate D-1** (the day *before* each transaction), applied leg-by-leg: cost at the
  buy's D-1 rate, proceeds at the sell's D-1 rate, each converted to PLN independently, then
  `PLN gain = PLN proceeds − PLN cost − deductible costs`. This is *not* the transacted rate the
  dashboard uses; the taxable PLN gain genuinely differs.
- **Tax year = calendar year**, by transaction date. Flat **19%** on the net annual gain.
- **Deductible costs** (commissions, `SEC fee`, etc.) reduce proceeds.

Deferred (not in the first cut, but real): loss carryforward (net losses carry up to 5 years to
offset future gains) and dividend/interest tax (domestic withheld final; foreign WHT + PL top-up).
Both omitted for now; add later if needed.

---

## Command group

`tax` is a command group (like `load`) over one shared tax engine — REGULAR-only filter, FIFO,
NBP D-1 — with thin view subcommands:

| Subcommand | Shows |
|---|---|
| `tax summary` | PIT-38 roll-up: realized proceeds, cost, deductible costs, net gain, tax @19% |
| `tax gains` | Itemized realized disposals (per closed lot) — the line-items behind the summary |
| `tax harvest` | Loss-harvesting candidates (FIFO loss, tax basis), ranked vs YTD gains |
| `tax lots <instr>` | FIFO lot / tax-basis inspection for one holding; verifies/plans a `harvest` sale |

Deferred (not in the first cut): dividend/interest tax handling, loss-carryforward tracking.

Shared options: `--year` (default current), `--estimate` (transacted FX until NBP wired). Output is
always PLN.

### `tax summary`

The PIT-38 roll-up. Sections:

```
Tax estimate 2026 — REGULAR pool (PLN/EUR/USD), NBP D-1 FX        ⚠ estimate, not filing-grade

Capital gains (PIT-38)
  realized proceeds (PLN)      …
  realized cost basis (PLN)    …
  deductible costs             …
  ── net realized gain         +X
  tax @ 19%                    =0.19·X
```

`--estimate` (shared group option, default until NBP wired): use transacted FX instead of NBP D-1,
banner stays. Once NBP D-1 rates are present and verified, drop the banner.

---

### `tax harvest`

Poland has **no wash-sale rule**, so a losing position can be sold to crystallise the loss and
immediately rebought — banking a realized loss to offset realized gains while keeping the economic
position. This view finds those opportunities.

```
Loss harvesting 2026 — REGULAR pool                              ⚠ estimate; deferral, not free money

YTD realized gain (tax basis):  +X PLN   →  tax due @19%: 0.19·X
Days to year-end: N

Candidate          FIFO loss if sold   Tax saved @19%   Cumulative offset
ETC Bitcoin              −6,200            1,178             −6,200
Pepco                    −1,400              266             −7,600
...
Selling the top K zeroes the taxable gain (harvest −X), saving 0.19·X.
```

Logic: for each open REGULAR instrument, compute the **FIFO-realizable loss** if sold now, in **tax
basis** (NBP D-1 cost vs current NBP proceeds); keep only net losses; rank; show cumulative offset
against YTD realized gains.

---

## Harvesting caveats — must be on the output, not buried

These are the traps that make naïve harvesting wrong:

1. **FX can flip the sign.** A position that's a loss in native currency can be a *gain* in PLN tax
   terms (and vice versa), because tax uses NBP D-1 on both legs. Harvest decisions **must** use the
   NBP tax-basis P/L, never the dashboard's native/economic number. This is the one place NBP D-1 is
   non-negotiable even for the estimate.
2. **FIFO only matters for *partial* sales.** Selling a position *in full* is order-independent —
   the realized loss is `total proceeds − total tax-basis cost`, identical under FIFO/LIFO/average
   (and equal to `(price − avg cost) × qty`). So `harvest`'s whole-position figure is exact. But if
   you sell only *part*, FIFO closes the oldest lots first, and the realized loss is no longer
   proportional — oldest-and-cheapest lots can even realize a *gain* on a position that's underwater
   on average. Use `tax lots <instr>` to see the FIFO-ordered lots and size a partial harvest.
3. **Deferral, not elimination.** Crystallising a loss resets cost basis lower, so a recovered rebuy
   carries a larger future gain. The benefit is real when offsetting gains you'd pay 19% on *now*,
   or banking a carryforward you expect to use within 5 years — not otherwise.
4. **By Dec 31.** Only transactions in the tax year count; the view shows days-to-year-end.
5. **Execution risk.** Commission is 0 on XTB stocks, but the sell→rebuy gap carries spread and
   price-move risk; rebuy promptly. The no-wash-sale rule is what makes the immediate rebuy a genuine
   disposal — that's the enabling fact, confirm it holds for the instrument.
6. **REGULAR only.** Harvesting in IKE/IKZE is pointless (already tax-free).

---

## Compute pipeline

1. Filter to REGULAR accounts; exclude IKE/IKZE.
2. **Realized:** FIFO-match lots closed in `--year` (per instrument, pool scope); convert each leg at
   NBP D-1; `gain = proceeds − cost − costs`.
3. Net annual realized; 19% on positive net.
4. **Harvest mode:** open REGULAR lots → tax-basis unrealized via NBP D-1 → FIFO-realizable loss per
   instrument → rank vs YTD realized gain. `lots <instr>` exposes the same per-lot detail for one name.
5. Render with the estimate banner unless NBP D-1 rates are present and verified.

---

## NBP dependency

This is the one command that genuinely needs NBP D-1 rates (historical, per transaction date — a
bounded set, cached). Until then it runs in `--estimate` mode on transacted FX, clearly banner-ed as
non-filing-grade. `api.nbp.pl` does per-date lookups, so fetch only the dates you actually transacted
on (and the buy dates of harvest candidates), then cache.
