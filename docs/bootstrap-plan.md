# Bootstrap Plan

## Tech Stack

| Concern | Choice | Reason |
|---|---|---|
| Package manager | **uv** | Modern, fast, single tool for venv + deps + lockfile |
| Python | **3.12** | dataclass `slots`, `match` statements, good type inference |
| CLI | **Typer + Rich** | Typer is declared option in design; Rich gives formatted tables |
| XLSX | **openpyxl** | Best-maintained, no Excel required |
| Canonical ledger | **JSONL** | Plaintext, git-diffable — explicitly preferred in design §9 |
| Query index | **DuckDB** | Better analytics than SQLite (window functions, group-by); still embedded/serverless |
| Lint + format | **ruff** | Replaces black + isort + flake8 in one fast tool |
| Type checking | **mypy** | Well-established, great dataclass support |
| Testing | **pytest + pytest-cov** | Standard |

---

## Project Structure

```
portfolio-tracker/
├── pyproject.toml           # uv project; all deps + tool config (ruff, mypy, pytest)
├── .python-version          # 3.12
├── .gitignore
├── src/
│   └── portfolio_tracker/
│       ├── __init__.py
│       ├── cli.py                   # Typer app; thin — just parses intent and calls core
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── events.py            # Event, EventType, SourceRef
│       │   ├── instruments.py       # Instrument, AssetClass
│       │   └── accounts.py          # Account, AccountType, Wrapper
│       ├── adapters/
│       │   ├── __init__.py
│       │   └── xtb/
│       │       ├── __init__.py
│       │       ├── discover.py      # Walk --paths, filename→account mapping table
│       │       ├── parse.py         # openpyxl row→raw dict; comment dispatcher
│       │       └── normalize.py     # raw dict → Event; Type→EventType mapping
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── ledger.py            # JSONL append/read; JSON ↔ Event serialization
│       │   └── index.py             # DuckDB: rebuild from ledger, insert-on-conflict-ignore
│       ├── reports/                 # Pure functions: events → result; no I/O
│       │   ├── __init__.py
│       │   ├── lots.py              # FIFO lot matching; LotMatchingPolicy protocol
│       │   ├── positions.py         # open positions, unrealized P/L
│       │   └── pnl.py               # realized P/L, dividends, fees
│       └── pricing/
│           ├── __init__.py
│           └── nbp.py               # FX provider (NBP API); FxProvider protocol
├── tests/
│   ├── conftest.py                  # shared fixtures (sample events, tmp ledger path)
│   ├── test_domain.py               # Event/Instrument/Account construction + serialization
│   ├── adapters/xtb/
│   │   ├── test_discover.py         # filename→account mapping edge cases
│   │   └── test_parse.py            # comment parser (OPEN BUY, Transfer, Deposit, etc.)
│   └── storage/
│       └── test_ledger.py           # round-trip JSONL; idempotent insert
└── data/xtb/                        # existing sample exports (untouched)
```

---

## Implementation Steps

### 1. Initialize uv project
```bash
uv init --no-workspace --python 3.12
```
Then add dependencies:
- **runtime**: `typer[all]`, `rich`, `openpyxl`, `duckdb`
- **dev**: `pytest`, `pytest-cov`, `ruff`, `mypy`

Configure ruff (line-length 100, select E/F/I/UP), mypy (strict), and pytest (src layout, cov) inside `pyproject.toml`.

Add `.python-version` = `3.12` and `.gitignore` (`.venv/`, `__pycache__/`, `*.egg-info`, `ledger.jsonl`, `*.duckdb`).

### 2. Domain types (`src/portfolio_tracker/domain/`)

**`events.py`**
- `SourceRef(frozen dataclass)`: `file: Path`, `sheet: str`, `row: int`, `raw: dict`
- `EventType(StrEnum)`: TRADE, DIVIDEND, FEE, INTEREST, DEPOSIT, WITHDRAWAL, FX_CONVERSION, TAX, CORPORATE_ACTION, TRANSFER
- `Event(frozen dataclass, slots=True)`: all fields from design §4.1 — `id`, `account_id`, `timestamp`, `type`, `instrument`, `quantity`, `price`, `amount`, `currency`, `fees`, `source`; plus `to_dict()`/`from_dict()` classmethods for JSONL serialization

**`instruments.py`**
- `AssetClass(StrEnum)`: EQUITY (only used in v1)
- `Instrument(frozen dataclass)`: `symbol`, `name`, `asset_class`, `quote_currency`, `isin`, `exchange` (all optional except symbol)

**`accounts.py`**
- `Wrapper(StrEnum)`: REGULAR, IKE, IKZE
- `Account(frozen dataclass)`: `account_id`, `broker`, `wrapper`, `base_currency`

All use `Decimal` for money/qty, never float.

### 3. Storage (`src/portfolio_tracker/storage/`)

**`ledger.py`**
- `append(events, path)` — opens JSONL in append mode, writes one JSON line per event
- `read(path) -> Iterator[Event]` — streams JSONL lines → Event objects
- JSON codec: datetime → ISO 8601, Decimal → string, Instrument/SourceRef → nested dict

**`index.py`**
- `rebuild(ledger_path, db_path)` — drops + recreates DuckDB tables from JSONL
- `insert(events, db_path)` — `INSERT OR IGNORE` by `id`
- Schema: `events` table mirrors Event fields; `instruments` table for dedup

### 4. XTB adapter (`src/portfolio_tracker/adapters/xtb/`)

**`discover.py`**
- `ACCOUNT_MAP`: filename prefix → `(Wrapper, base_ccy)` (PLN_→REGULAR/PLN, EUR_→REGULAR/EUR, USD_→REGULAR/USD, IKE_→IKE/PLN, IKZE_→IKZE/PLN)
- `discover(paths) -> list[tuple[Path, Account]]` — walks dirs, matches prefix, warns + skips unknowns

**`parse.py`**
- `parse_workbook(path, account) -> list[dict]` — locate "Cash operations" sheet, find header row, parse each row into raw dict. Skip the trailing `Total` row.
- `parse_comment(type_str, comment) -> dict` — dispatcher per `type_str`:
  - `Stock purchase` / `Stock sell` → parse `(OPEN|CLOSE) (BUY|SELL) {qty}[/{total}] @ {price}`
  - `Transfer` → parse `Exchange rate:{r}`
  - `Deposit` → classify provider keyword → `{is_external: bool}`
  - `Subaccount transfer` → parse from/to ids
  - `Dividend*` → parse native ccy/rate if present

**`normalize.py`**
- `TYPE_MAP`: `type_str → EventType`
- `normalize(raw, account) -> Event | None`
- `load(paths, dry_run=False) -> list[Event]` — orchestrates discover → parse → normalize

### 5. CLI (`src/portfolio_tracker/cli.py`)

Typer app:
- `tracker load xtb --paths PATH... [--dry-run]`
- `tracker accounts`
- Stubs: `positions`, `pnl`, `dividends`, `summary`

Entry point in `pyproject.toml`: `tracker = "portfolio_tracker.cli:app"`

### 6. Tests

- `test_domain.py`: round-trip `Event → dict → Event`; Decimal preserved; frozen immutability
- `test_parse.py`: unit tests for `parse_comment` with every known `type_str` + edge cases
- `test_discover.py`: known prefixes map correctly; unknown prefix skipped with warning
- `test_ledger.py`: append + read round-trip; duplicate id insert is idempotent

---

## Verification
```bash
uv run pytest --cov=portfolio_tracker
uv run ruff check src/ tests/
uv run mypy src/
uv run tracker load xtb --paths data/xtb --dry-run
```
