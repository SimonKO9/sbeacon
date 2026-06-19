# `summary` Command — Implementation Brief

Companion to `DESIGN.md` (§10 commands) and `PRICING.md`. `summary` is the **at-a-glance
roll-up** — one screen answering "where do I stand," per account and globally. The detail lives
in the drill-down commands (`positions`, `pnl`, `allocation`, `performance`); `summary` must not
duplicate them, just tie their headline numbers together.

All figures are pure functions over the event ledger plus the price/FX layer (`PRICING.md`).
Reporting currency defaults to PLN.

---

## What it shows

```
Portfolio summary — as of 2026-06-14 (prices EOD, FX NBP)        reporting: PLN

Account   Value      Net in     P/L         P/L %    Cash
PLN       142,310    120,000    +22,310     +18.6%    6.79
EUR        38,920     35,500     +3,420      +9.6%   210.40
USD        61,540     54,000     +7,540     +14.0%    88.10
IKE        58,200     44,260    +13,940     +31.5%    0.67
IKZE       12,880     11,956       +924      +7.7%    0.00
─────────────────────────────────────────────────────────
TOTAL     313,850    231,716    +82,134     +35.4%   306.xx

TOTAL P/L decomposition:
  unrealized   +61,200    realized   +14,980    dividends  +4,310
  interest        +290    fees          −180    taxes      −2,460    fx-on-cash  +y

money-weighted return (XIRR): +14.2% p.a.        (simple P/L: +35.4%)
```

(Numbers illustrative.) Rows are the five accounts plus a `TOTAL`; with `--by=wrapper` the rows
collapse to `REGULAR` (PLN+EUR+USD pool), `IKE`, `IKZE`.

### Column definitions

- **Value** — open-position market value + cash, in reporting currency. Market value =
  Σ `qty × current price × FX→PLN` over open lots (FIFO); cash = Σ cash events by currency → PLN.
- **Net in** — own capital contributed (see scope rule below).
- **P/L** — `Value − Net in`, absolute. This single subtraction is the honest total-return number:
  every dividend, fee, tax, and FX move already flowed through cash or position value, so it
  captures all of them without summing components.
- **P/L %** — `P/L / Net in`.
- **Cash** — free cash (reconciles against each file's `Total` row; flag a delta if off).

---

## Critical rule: "Net in" is scope-dependent

Get this wrong and the global P/L% is meaningless.

- **Global scope** counts **external capital only** — bank/BLIK/PayU deposits minus external
  withdrawals. In this account structure that's essentially all PLN hitting the PLN account.
  Internal moves (PLN→EUR/USD currency conversions, PLN→IKE/IKZE funding, subaccount transfers)
  net to zero and **must not** count as new capital.
- **Per-account scope** counts everything that funded that account, including internal transfers
  in (e.g. the IKE's "Net in" is the transfer it received). Internal globally, but it's how you'd
  judge that account in isolation.

So the same `Net in` cell is computed differently at account vs total scope. Classification of each
cash row as external/internal comes from the comment-signature logic in `DESIGN.md` §8.

---

## P/L decomposition = integrity check

The decomposition line is informative *and* a self-check. The identity must hold:

```
unrealized + realized + dividends + interest − fees − taxes + fx_on_cash  ==  Value − Net in
```

- **unrealized** — open positions: market value − cost basis.
- **realized** — closed FIFO lots, lifetime (includes IKE/IKZE gains; they're economic gain even
  though untaxed).
- **dividends, interest** — income received (gross inside IKE; net of withholding elsewhere).
- **fees, taxes** — actually paid (`SEC fee`, `Free funds interest tax`, `Withholding tax`).
- **fx_on_cash** — FX gain/loss on non-PLN cash balances revalued to reporting currency.

`summary` should **assert** this equality and flag the delta rather than print two numbers that
silently disagree — same spirit as the cash reconcile.

---

## Compute pipeline (pure over ledger + price/FX)

1. Derive open lots per `(account, instrument)` by FIFO → quantity + cost basis.
2. Fetch current prices (cached EOD) and FX→PLN for held instruments and non-PLN cash.
3. Per holding: market value + unrealized P/L; sum per account.
4. Cash per account (Σ cash events by currency → PLN); reconcile vs the `Total` row.
5. Realized P/L (closed FIFO lots), dividends, interest, fees, taxes — lifetime.
6. Net external capital: classify deposits/withdrawals external vs internal; net internal to zero
   at global scope, keep gross at account scope.
7. Roll up: account → wrapper pool (PLN/EUR/USD = REGULAR) → global; compute %, XIRR.
8. Render with the as-of stamp; assert the decomposition identity.

---

## Flags

- `--reporting-ccy=PLN` — currency everything rolls to (default PLN).
- `--by=account|wrapper` — row grouping (default `account`). `wrapper` collapses PLN/EUR/USD into
  `REGULAR`.
- `--as-of=YYYY-MM-DD` — historical snapshot: fold the ledger up to that date and value at that
  date's prices/FX. Requires historical prices from the price layer (`history()`); defaults to
  today. (Stretch — the architecture supports it but it adds a historical-price dependency.)

---

## Honesty / edge notes (put in the spec)

- **Economic value, pre future tax.** No deduction for tax owed on a future IKE/IKZE withdrawal or
  on unrealized REGULAR gains — that's a separate liability view.
- **Label the return metric.** Headline is XIRR (money-weighted), the right metric for irregular
  contributions; show it next to the simple P/L%, since they answer different questions.
- **Staleness.** Value depends on cached EOD prices/FX — always print the as-of date and flag stale
  or missing quotes rather than silently using yesterday's.
- **Reconcile surfacing.** If derived cash ≠ `Total` row, or the decomposition identity doesn't
  hold, show a visible warning on the relevant row.
