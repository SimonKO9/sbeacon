# portfolio-tracker

Personal investment portfolio tracker. Ingests XTB broker exports, derives open positions and P&L, exposes a CLI.

## Setup

```bash
uv sync
```

## CLI

```bash
# Preview what would be imported (no writes)
uv run tracker load xtb --paths data/xtb --dry-run

# Import into ledger + index
uv run tracker load xtb --paths data/xtb

# List loaded accounts
uv run tracker accounts
```

## Tests

```bash
uv run pytest                        # all tests with coverage
uv run pytest -x                     # stop on first failure
uv run pytest tests/adapters/        # one module
```

## Linting / type checking

```bash
uv run ruff check src/ tests/
uv run mypy src/
```

## Dev environment

Requires [uv](https://docs.astral.sh/uv/). On Bazzite, `uv` lives inside the `dev` Distrobox — enter it first:

```bash
distrobox enter dev
```

A `.devcontainer/devcontainer.json` is also provided for VS Code Dev Containers (Python 3.12 + uv).

## Project layout

```
src/portfolio_tracker/
├── cli.py              # Typer entry point
├── domain/             # Event, Instrument, Account — no I/O
├── adapters/xtb/       # XTB .xlsx ingestion
├── storage/            # JSONL ledger + DuckDB index
├── reports/            # Pure analytics functions (WIP)
└── pricing/            # FX providers (WIP)
```

Data files are in `data/xtb/`. The ledger (`data/ledger.jsonl`) and index (`data/index.duckdb`) are git-ignored.
