#!/usr/bin/env bash
set -euo pipefail

rm -f data/ledger.jsonl data/index.duckdb
uv run tracker load xtb --paths data/xtb
