from __future__ import annotations

from decimal import Decimal

import pytest

from portfolio_tracker.config import (
    AppConfig,
    ExtraAsset,
    clear_instrument,
    load_config,
    normalized_targets,
    save_config,
    set_allocation,
    set_instrument_asset_class,
    set_instrument_role,
)


def test_load_config_generates_default(tmp_path):
    path = tmp_path / "config.yaml"
    cfg, generated = load_config(path)
    assert generated is True
    assert path.exists()
    assert cfg.reporting_currency == "PLN"
    assert "core" in cfg.targets
    assert cfg.instruments == {}


def test_load_config_round_trip(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = AppConfig(
        reporting_currency="EUR",
        targets={"core": 0.6, "cash": 0.4},
        instruments={"FOO.PL": {"role": "satellite"}},
    )
    save_config(cfg, path)

    loaded, generated = load_config(path)
    assert generated is False
    assert loaded.reporting_currency == "EUR"
    assert loaded.targets == {"core": 0.6, "cash": 0.4}
    assert loaded.instruments == {"FOO.PL": {"role": "satellite"}}


def test_normalized_targets_sums_to_one():
    cfg = AppConfig(targets={"core": 0.4, "satellite": 0.6})
    norm = normalized_targets(cfg)
    assert abs(sum(norm.values()) - 1.0) < 1e-9
    assert abs(norm["core"] - 0.4) < 1e-9
    assert abs(norm["satellite"] - 0.6) < 1e-9


def test_normalized_targets_unequal_raw():
    cfg = AppConfig(targets={"core": 0.5, "satellite": 0.25})
    norm = normalized_targets(cfg)
    assert abs(norm["core"] - 2 / 3) < 1e-9
    assert abs(norm["satellite"] - 1 / 3) < 1e-9


def test_normalized_targets_zero_sum():
    cfg = AppConfig(targets={"core": 0.0, "cash": 0.0})
    norm = normalized_targets(cfg)
    assert norm == {"core": 0.0, "cash": 0.0}


def test_set_allocation_valid():
    cfg = AppConfig()
    set_allocation(cfg, "core", 0.5)
    assert cfg.targets["core"] == 0.5


def test_set_allocation_invalid_role():
    cfg = AppConfig()
    with pytest.raises(ValueError, match="Unknown role"):
        set_allocation(cfg, "bogus", 0.1)


def test_set_allocation_negative_weight():
    cfg = AppConfig()
    with pytest.raises(ValueError, match="non-negative"):
        set_allocation(cfg, "core", -0.1)


def test_set_instrument_role_valid():
    cfg = AppConfig()
    set_instrument_role(cfg, "FOO.PL", "satellite")
    assert cfg.instruments["FOO.PL"]["role"] == "satellite"


def test_set_instrument_role_invalid():
    cfg = AppConfig()
    with pytest.raises(ValueError, match="Unknown role"):
        set_instrument_role(cfg, "FOO.PL", "bad-role")


def test_set_instrument_asset_class_valid():
    cfg = AppConfig()
    set_instrument_asset_class(cfg, "CDR.PL", "equity")
    assert cfg.instruments["CDR.PL"]["asset_class"] == "equity"


def test_set_instrument_asset_class_alias():
    cfg = AppConfig()
    set_instrument_asset_class(cfg, "CDR.PL", "stock")
    assert cfg.instruments["CDR.PL"]["asset_class"] == "equity"


def test_set_instrument_asset_class_invalid():
    cfg = AppConfig()
    with pytest.raises(ValueError, match="Unknown asset class"):
        set_instrument_asset_class(cfg, "CDR.PL", "junk")


def test_set_instrument_merges_fields():
    cfg = AppConfig()
    set_instrument_role(cfg, "FOO.PL", "core")
    set_instrument_asset_class(cfg, "FOO.PL", "equity")
    assert cfg.instruments["FOO.PL"] == {"role": "core", "asset_class": "equity"}


def test_clear_instrument():
    cfg = AppConfig(instruments={"FOO.PL": {"role": "core"}})
    clear_instrument(cfg, "FOO.PL")
    assert "FOO.PL" not in cfg.instruments


def test_clear_instrument_missing_is_noop():
    cfg = AppConfig()
    clear_instrument(cfg, "NONEXISTENT.PL")  # should not raise


# ── extra-assets ──────────────────────────────────────────────────────────────

def test_extra_assets_round_trip(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = AppConfig(
        extra_assets={
            "real-assets": [ExtraAsset(name="Apartment", value=Decimal("700000"), currency="PLN")],
            "reserves": [ExtraAsset(name="Emergency fund", value=Decimal("50000"), currency="PLN")],
        }
    )
    save_config(cfg, path)

    loaded, _ = load_config(path)
    ra = loaded.extra_assets["real-assets"]
    assert len(ra) == 1
    assert ra[0].name == "Apartment"
    assert ra[0].value == Decimal("700000")
    assert ra[0].currency == "PLN"

    res = loaded.extra_assets["reserves"]
    assert len(res) == 1
    assert res[0].name == "Emergency fund"


def test_extra_assets_currency_defaults_to_reporting(tmp_path):
    import yaml
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump({
        "reporting_currency": "EUR",
        "extra-assets": {"real-assets": [{"name": "House", "value": 300000}]},
    }))
    cfg, _ = load_config(path)
    assert cfg.extra_assets["real-assets"][0].currency == "EUR"


def test_extra_assets_invalid_key(tmp_path):
    import yaml
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump({"extra-assets": {"bad-key": []}}))
    with pytest.raises(ValueError, match="Unknown extra-assets key"):
        load_config(path)


def test_extra_assets_empty_by_default(tmp_path):
    path = tmp_path / "config.yaml"
    cfg, _ = load_config(path)
    assert cfg.extra_assets == {}


def test_set_instrument_asset_class_real_estate():
    cfg = AppConfig()
    set_instrument_asset_class(cfg, "SOME.PL", "real-estate")
    assert cfg.instruments["SOME.PL"]["asset_class"] == "real-estate"
