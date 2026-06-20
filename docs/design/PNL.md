# `pnl` Command — Implementation Brief

Companion to `DESIGN.md` (§7 lot matching, §10 commands), `SUMMARY.md`, and `PRICING.md`.

`pnl` is `summary`'s P/L decomposition made **sliceable** (by dimension) and **time-bounded**
(by period), with realized / unrealized / income split out. It shares the same underlying numbers
as `summary` and **must reconcile to `summary`'s total P/L** — if it doesn't, one of them is wrong.
Reporting currency defaults to PLN; FX handling is inherited from `summary` as-is (per-account
foreign P/L is native-converted; the `fx/cash` residual carries the difference — a documented
simplification, not a bug).

---

## The one decision that matters: period semantics

P/L components don't share a temporal nature. Conflating them is how these reports mislead.

- **Realized P/L and income** (dividends, interest, fees, taxes) are **flows** with a clear date —
  the sale date, the payment date. `--period=2025` cleanly means "lots closed in 2025" and "income
  received in 2025." Ledger-only, no prices needed.
- **Unrealized P/L** is a **snapshot** (market value − cost basis *as of a moment*), not a flow.
  "Unrealized in 2025" only has a rigorous meaning as the *change* over the window (mark at start vs
  end), which needs historical prices at the boundaries.

**Default behavior:** realized + income are period-filtered by event date; **unrealized is always
the current snapshot** on positions still open, labelled as such. A period report reads as "what got
booked during this window" + "what's sitting in paper gains right now" — two honestly-different
things, never fused into a fake "period total." The rigorous boundary-mark mode (change in
unrealized over the window, using historical prices) is an **optional future flag**, not the default.

---

## What it shows

`--by=instrument`, lifetime (default):

```
P/L by instrument — lifetime (reporting: PLN)
Instrument          Realized  Unrealized  Income    Total    Return%
ServiceNow                 0    +12,400        0    +12,400     +24%
Energy Fuels          +1,290     +3,400      +12     +4,702     +31%
Galaxy Phys. Bitcoin  -6,200          0        0     -6,200     -38%
...
──────────────────────────────────────────────────────────────
fx/cash                                              -8,105
TOTAL                                               +167,289   (ties to summary)
```

`--period=2025 --by=account` (note the explicit split):

```
P/L for 2025 — realized + income are during-period; unrealized is current snapshot
Account   Realized  Dividends  Interest  Fees  Taxes  │  Unrealized (now)
...
```

### Columns

- **Realized** — closed FIFO lots, proceeds − matched cost, in reporting currency, dated at sale.
- **Unrealized** — current market value − cost basis on open lots (snapshot; period-independent).
- **Income** — dividends + interest received (gross inside IKE, net of withholding elsewhere).
  Optionally split fees/taxes out as their own columns.
- **Total** — realized + unrealized + income − costs.
- **Return%** — against the relevant cost base; **label which base** (realized → cost of closed
  lots; unrealized → cost of open lots; total → total cost basis). Don't print an unlabelled %.

---

## Grouping (`--by`)

Just the group key; same aggregation underneath:

| `--by` | Use |
|---|---|
| `instrument` (default) | What's driving P/L — the workhorse view |
| `account` | Per-account, with the realized/unrealized/income split |
| `asset-class` | STOCK / ETC / ETF sleeves (from `Category`) |
| `currency` | PLN vs USD vs EUR instrument performance |
| `wrapper` | REGULAR pool vs IKE vs IKZE |
| `sub-account` | Investment Plans vs My Trades (`Product`) |

Sorted by total descending; `--top=N` for biggest movers/losers.

---

## Flags

- `--period=YYYY` or `--from=YYYY-MM-DD --to=YYYY-MM-DD` — default lifetime.
- `--realized | --unrealized | --income` — isolate components (default: all). `--unrealized`
  ignores `--period` and states so.
- `--by=instrument|account|asset-class|currency|wrapper|sub-account` — default `instrument`.
- `--sort=total|realized|unrealized`, `--top=N`.
- `--reporting-ccy=PLN`.

---

## Compute pipeline (same engine as `summary`, sliced)

1. FIFO-match closed lots → realized P/L events, each dated at the sale, tagged with
   instrument / account / currency / wrapper / sub-account.
2. Open lots + current prices/FX → current unrealized per group.
3. Income/cost events from the ledger, dated.
4. **Period filter applies to realized + income only;** unrealized stays current.
5. Group by the chosen key, aggregate components, compute return% against the labelled cost base.
6. Append the `fx/cash` residual + TOTAL; **assert TOTAL equals `summary`'s P/L** (carry the same
   residual line so the two views agree).
7. Sort / `--top` / render.

---

## Honesty / edge notes (put in the spec)

- **Unrealized is a snapshot** — never silently period-attributed. Label it on every period report.
- **Realized depends on the lot policy** (FIFO, §7); economic-only inside IKE/IKZE since those
  sells aren't taxed.
- **Income is gross in IKE**, net of withholding elsewhere — already encoded in the event
  categories (`DESIGN.md` §8).
- **Must reconcile to `summary`.** `pnl` totals + `fx/cash` = `summary` total P/L, or something is
  mis-signed/miscategorized.
- **Return% needs a labelled denominator** — realized, unrealized, and total use different cost
  bases; an unlabelled % is meaningless.
