# Using portfolio-tracker with XTB

This guide covers how to export your transaction history from XTB and load it into the tracker.

---

## 1. Export your data from XTB

The tracker reads the **Cash Operations** sheet from XTB's `.xlsx` history export. You need one export per account (PLN, EUR, USD, IKE, IKZE).

**Steps in the XTB web platform:**

1. Log in to the XTB web app.
2. Go to **Account history** and click **Export (new)** → **New Report**.
3. Choose dates — ideally a single export covering all positions since account inception. A partial export will produce wrong opening positions because the tracker rebuilds holdings from scratch.
4. Select **all accounts** (PLN, EUR, USD, IKE, IKZE — whichever you hold).
5. Click **Generate**, then download the generated archive when it's ready.
6. Extract the archive into `data/xtb/` in this project.

---

## 2. Place your files

Extract the downloaded archive into `data/xtb/`. The tracker identifies each account from the **filename prefix** (the part before the first `_`) — XTB's default export names already carry the right prefix:

| Prefix | Account type | Base currency |
|---|---|---|
| `PLN_` | Regular | PLN |
| `EUR_` | Regular | EUR |
| `USD_` | Regular | USD |
| `IKE_` | IKE | PLN |
| `IKZE_` | IKZE | PLN |

No renaming is needed. Files with an unrecognised prefix are skipped with a warning.

---

## 3. Preview before importing

Run with `--dry-run` first to verify the files parse cleanly without writing anything:

```bash
tracker load xtb --paths data/xtb --dry-run
```

The dry-run output shows:
- Files discovered and the account each maps to
- Row counts by event type (buys, sells, dividends, fees, …)
- Date range of events found — verify the earliest date matches your account's inception
- Any rows that failed to parse (unrecognised `Type`, comments that didn't match the expected format)
- Any unrecognised `Type` strings not yet in the adapter

Fix any warnings before proceeding. The most common issue is a `Date from` that doesn't reach account inception, which means opening positions will be wrong.

---

## 4. Load

```bash
tracker load xtb --paths data/xtb
```

The loader is **idempotent**: re-importing an overlapping export (or the same file twice) will not double-count transactions. Each row's XTB `ID` column is used as the stable event identity (`{account}:{ID}`).

After loading you can list what's in the database:

```bash
tracker accounts   # accounts loaded and event counts
```

---

## 5. Multiple exports / re-exports

Because imports are idempotent, you can safely re-export a wider date range from XTB and re-run `load`. Only new rows will be added. This is the recommended workflow when XTB adds retroactive corrections or you extend the date range.

Dated filename suffixes help you track which exports you have on disk, but they don't affect loading — two files with the same prefix are merged into the same account.

---

## 6. Known caveats

**Full history required.** The tracker derives holdings from zero by replaying Cash Operations. A partial export (one that doesn't start at account inception) will produce incorrect open positions and cost basis.

**Corporate actions are not supported yet.** Stock splits and ticker changes don't appear as Cash Operations rows and are not currently handled by the tracker. If a held stock has split, the tracker will show a wrong quantity. This will be addressed in a future release.

**`.UK` suffix instruments are not always GBP.** Currency is resolved per instrument, not from the ticker suffix — `R2US.UK` and `BCHN.UK` are USD-denominated despite the `.UK` suffix. The adapter handles this correctly; just be aware if you inspect raw data.

**Cash reconciliation.** After loading, the tracker compares its derived free-cash balance against the trailing `Total` row in each export. A mismatch (flagged in `reconcile` output) usually means an unparsed row or a corporate action gap.
