from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from portfolio_tracker.domain.instruments import AssetClass, Role

ROLES: frozenset[str] = frozenset(r.value for r in Role)
ASSET_CLASSES: frozenset[str] = frozenset(a.value for a in AssetClass)
ASSET_CLASS_ALIASES: dict[str, str] = {"stock": "equity"}
VALID_EXTRA_ASSET_KEYS: frozenset[str] = frozenset({"real-assets", "reserves"})

DEFAULT_TARGETS: dict[str, float] = {
    "core": 0.40,
    "satellite": 0.25,
    "thematic": 0.10,
    "real-assets": 0.10,
    "crypto": 0.05,
    "fixed-income": 0.05,
    "cash": 0.05,
}


@dataclass
class ExtraAsset:
    name: str
    value: Decimal
    currency: str  # reporting_currency if omitted in YAML


@dataclass
class AppConfig:
    reporting_currency: str = "PLN"
    targets: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TARGETS))
    # keyed by ticker (or ISIN); sparse overrides with keys "role"/"asset_class"
    instruments: dict[str, dict[str, str]] = field(default_factory=dict)
    # keyed by "real-assets" or "reserves"
    extra_assets: dict[str, list[ExtraAsset]] = field(default_factory=dict)


def load_config(path: Path) -> tuple[AppConfig, bool]:
    """Load config from path. Returns (config, was_generated)."""
    if not path.exists():
        cfg = AppConfig()
        save_config(cfg, path)
        return cfg, True

    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    reporting_currency = str(data.get("reporting_currency", "PLN"))

    extra_assets: dict[str, list[ExtraAsset]] = {}
    for key, items in (data.get("extra-assets") or {}).items():
        if key not in VALID_EXTRA_ASSET_KEYS:
            raise ValueError(
                f"Unknown extra-assets key {key!r}. Valid: {sorted(VALID_EXTRA_ASSET_KEYS)}"
            )
        extra_assets[key] = [
            ExtraAsset(
                name=str(item["name"]),
                value=Decimal(str(item["value"])),
                currency=str(item.get("currency", reporting_currency)),
            )
            for item in (items or [])
        ]

    return AppConfig(
        reporting_currency=reporting_currency,
        targets={str(k): float(v) for k, v in data.get("targets", DEFAULT_TARGETS).items()},
        instruments={
            str(k): {str(ik): str(iv) for ik, iv in (v or {}).items()}
            for k, v in data.get("instruments", {}).items()
        },
        extra_assets=extra_assets,
    ), False


def save_config(cfg: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "reporting_currency": cfg.reporting_currency,
        "targets": dict(cfg.targets),
        "instruments": {k: dict(v) for k, v in cfg.instruments.items()},
    }
    if cfg.extra_assets:
        data["extra-assets"] = {
            key: [
                {"name": a.name, "value": float(a.value), "currency": a.currency}
                for a in assets
            ]
            for key, assets in cfg.extra_assets.items()
        }
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def normalized_targets(cfg: AppConfig) -> dict[str, float]:
    """Return targets normalized so they sum to 1."""
    total = sum(cfg.targets.values())
    if total == 0:
        return {k: 0.0 for k in cfg.targets}
    return {k: v / total for k, v in cfg.targets.items()}


# ── mutation helpers (each caller must save_config after) ────────────────────

def set_allocation(cfg: AppConfig, role: str, weight: float) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}. Valid roles: {sorted(ROLES)}")
    if weight < 0:
        raise ValueError("Weight must be non-negative")
    cfg.targets[role] = weight


def set_instrument_role(cfg: AppConfig, ticker: str, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}. Valid roles: {sorted(ROLES)}")
    cfg.instruments.setdefault(ticker, {})["role"] = role


def set_instrument_asset_class(cfg: AppConfig, ticker: str, asset_class: str) -> None:
    resolved = ASSET_CLASS_ALIASES.get(asset_class, asset_class)
    if resolved not in ASSET_CLASSES:
        raise ValueError(
            f"Unknown asset class {asset_class!r}. Valid: {sorted(ASSET_CLASSES)}"
        )
    cfg.instruments.setdefault(ticker, {})["asset_class"] = resolved


def clear_instrument(cfg: AppConfig, ticker: str) -> None:
    cfg.instruments.pop(ticker, None)
