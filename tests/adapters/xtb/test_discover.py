from __future__ import annotations

from pathlib import Path

import pytest

from portfolio_tracker.adapters.xtb.discover import discover
from portfolio_tracker.domain.accounts import Wrapper


def test_known_prefixes_map_correctly(tmp_path: Path) -> None:
    (tmp_path / "PLN_50320481_2006-01-01_2026-06-14.xlsx").touch()
    (tmp_path / "IKE_51109778_2006-01-01_2026-06-14.xlsx").touch()
    (tmp_path / "IKZE_53051164_2006-01-01_2026-06-14.xlsx").touch()

    results = discover([tmp_path])
    accounts = {a.account_id: a for _, a in results}

    assert "XTB_PLN" in accounts
    assert accounts["XTB_PLN"].wrapper == Wrapper.REGULAR
    assert accounts["XTB_PLN"].base_currency == "PLN"

    assert "XTB_IKE" in accounts
    assert accounts["XTB_IKE"].wrapper == Wrapper.IKE
    assert accounts["XTB_IKE"].base_currency == "PLN"

    assert "XTB_IKZE" in accounts
    assert accounts["XTB_IKZE"].wrapper == Wrapper.IKZE


def test_eur_usd_accounts(tmp_path: Path) -> None:
    (tmp_path / "EUR_50324884.xlsx").touch()
    (tmp_path / "USD_50972260.xlsx").touch()

    results = discover([tmp_path])
    accounts = {a.account_id: a for _, a in results}

    assert accounts["XTB_EUR"].base_currency == "EUR"
    assert accounts["XTB_EUR"].wrapper == Wrapper.REGULAR
    assert accounts["XTB_USD"].base_currency == "USD"


def test_unknown_prefix_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "CRYPTO_12345.xlsx").touch()
    results = discover([tmp_path])
    assert results == []
    assert "CRYPTO" in caplog.text


def test_non_xlsx_ignored(tmp_path: Path) -> None:
    (tmp_path / "PLN_export.csv").touch()
    (tmp_path / "PLN_export.txt").touch()
    assert discover([tmp_path]) == []


def test_recursive_walk(tmp_path: Path) -> None:
    sub = tmp_path / "account1"
    sub.mkdir()
    (sub / "PLN_50320481.xlsx").touch()
    results = discover([tmp_path])
    assert len(results) == 1
