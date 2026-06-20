from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from portfolio_tracker.domain.events import Event, EventType
from portfolio_tracker.domain.instruments import AssetClass, Role
from portfolio_tracker.reports.positions import compute_positions

if TYPE_CHECKING:
    from portfolio_tracker.config import AppConfig
    from portfolio_tracker.pricing.provider import Quote

TagSource = Literal["auto", "manual", "review", "untagged"]


@dataclass
class TaggedInstrument:
    ticker: str
    name: str | None
    asset_class: AssetClass | None
    role: Role | None
    source: TagSource


@dataclass
class AllocationRow:
    bucket: str
    value_pln: Decimal
    weight: Decimal         # 0–1 fraction
    target: Decimal | None  # normalized target 0–1 (role lens only)
    drift: Decimal | None   # weight − target (pp)
    rebalance: Decimal | None  # (target − weight) × total PLN


@dataclass
class AllocationResult:
    rows: list[AllocationRow]
    total_pln: Decimal
    untagged_pln: Decimal
    lens: str  # "role" or "asset-class"


# ── name-keyword heuristics ───────────────────────────────────────────────────

_CRYPTO_NAME_KW = {"bitcoin", "btc", "ethereum", "ether", "blockchain", "crypto"}
_CRYPTO_TICKER_KW = {"xxbt", "xeth", "btcx", "ethx"}
_COMMODITY_KW = {"gold", "silver", "palladium", "platinum", "oil", "commodity", "commodities", "xau", "xag"}
_FIXED_INCOME_KW = {"bond", "bonds", "treasury", "gilt", "bund", "btp", "fixed income", "aggregate"}
_BROAD_KW = {
    "s&p 500", "sp500", "s&p500",
    "msci world", "msci acwi",
    "nasdaq", "ftse 100", "ftse all",
    "total market", "all world", "all-world",
    "world equity", "global equity",
    "emerging market", "msci em",
    "stoxx 600", "euro stoxx",
}
_ETF_KW = {"etf", "etc", "etp", "ucits", "fund", "index"}
# European fund exchanges where 4+ char tickers are almost exclusively ETFs/ETCs
# (German stocks on Xetra, Dutch on Euronext Amsterdam use 2-3 char tickers)
_EU_FUND_EXCHANGES = {"de", "nl", "as"}
# Ticker prefixes used by ETF/ETC/ETN wrappers (e.g. ETFBNDXPL.PL, ETNVIRBTCP.PL)
_ETF_TICKER_PREFIXES = ("etf", "etc", "etn")


def _is_etf_like(ticker: str, name: str) -> bool:
    """Return True if the instrument is almost certainly an ETF/ETC/ETN."""
    t = ticker.lower()
    n = name.lower()
    if any(kw in n for kw in _ETF_KW):
        return True
    # Ticker starts with ETF/ETC/ETN wrapper prefix
    prefix = t.split(".")[0]
    if any(prefix.startswith(p) for p in _ETF_TICKER_PREFIXES):
        return True
    # European fund exchange + long ticker: .DE/.NL/.AS tickers with 4+ chars
    # before the dot are almost never single stocks — German/Dutch stocks use
    # 2-3 char tickers (DTE, VOW, PHI, etc.)
    parts = t.rsplit(".", 1)
    if len(parts) == 2 and parts[1] in _EU_FUND_EXCHANGES and len(parts[0]) >= 4:
        return True
    return False


def _auto_tag(ticker: str, name: str | None) -> tuple[AssetClass | None, Role | None, TagSource]:
    n = (name or "").lower()
    t = ticker.lower()

    for kw in _CRYPTO_NAME_KW:
        if kw in n:
            return AssetClass.CRYPTO, Role.CRYPTO, "auto"
    for kw in _CRYPTO_TICKER_KW:
        if kw in t:
            return AssetClass.CRYPTO, Role.CRYPTO, "auto"

    for kw in _COMMODITY_KW:
        if kw in n:
            return AssetClass.COMMODITY, Role.REAL_ASSETS, "auto"

    for kw in _FIXED_INCOME_KW:
        if kw in n:
            return AssetClass.FIXED_INCOME, Role.FIXED_INCOME, "auto"

    # Broad-market check before ETF-marker check: XTB truncates names so ETFs
    # often lack "ETF"/"UCITS" but still contain the index name.
    if any(kw in n for kw in _BROAD_KW):
        return AssetClass.EQUITY, Role.CORE, "auto"

    if _is_etf_like(ticker, name or ""):
        # Known ETF/ETC structure but no broad-market keyword → sector/thematic, flag for review
        return AssetClass.EQUITY, Role.THEMATIC, "review"

    if n or t:
        # Has some identity, no ETF signal → treat as single stock
        return AssetClass.EQUITY, Role.SATELLITE, "auto"

    return None, None, "untagged"


def tag_instrument(
    ticker: str,
    name: str | None,
    overrides: dict[str, str],
) -> TaggedInstrument:
    """Resolve effective asset_class and role; manual overrides win over auto."""
    auto_ac, auto_role, auto_source = _auto_tag(ticker, name)

    override_ac_str = overrides.get("asset_class")
    override_role_str = overrides.get("role")

    if override_ac_str is None and override_role_str is None:
        return TaggedInstrument(
            ticker=ticker,
            name=name,
            asset_class=auto_ac,
            role=auto_role,
            source=auto_source,
        )

    ac = AssetClass(override_ac_str) if override_ac_str else auto_ac
    role = Role(override_role_str) if override_role_str else auto_role
    return TaggedInstrument(
        ticker=ticker,
        name=name,
        asset_class=ac,
        role=role,
        source="manual",
    )


def get_tagged_instruments(
    events: list[Event],
    instrument_overrides: dict[str, dict[str, str]],
) -> list[TaggedInstrument]:
    """Return tagged instruments for all currently held positions."""
    trade_events = [e for e in events if e.type == EventType.TRADE]
    positions = compute_positions(trade_events)

    name_by_symbol: dict[str, str | None] = {}
    for e in events:
        if e.instrument and e.instrument.symbol not in name_by_symbol:
            name_by_symbol[e.instrument.symbol] = e.instrument.name

    # deduplicate: same ticker can appear in multiple accounts
    seen: set[str] = set()
    result: list[TaggedInstrument] = []
    for pos in positions:
        if pos.symbol in seen:
            continue
        seen.add(pos.symbol)
        name = name_by_symbol.get(pos.symbol)
        overrides = instrument_overrides.get(pos.symbol, {})
        result.append(tag_instrument(pos.symbol, name, overrides))
    return result


# ── cash balance helper ───────────────────────────────────────────────────────

def _cash_pln(events: list[Event], fx_rates: dict[str, Decimal]) -> Decimal:
    """Net cash across all accounts in PLN (same logic as summary)."""
    account_base: dict[str, str] = {}
    for e in events:
        account_base[e.account_id] = e.currency

    cash_native: dict[str, Decimal] = {}
    for e in events:
        acc = e.account_id
        cash_native[acc] = cash_native.get(acc, Decimal(0)) + e.amount

    total = Decimal(0)
    for acc, native in cash_native.items():
        ccy = account_base.get(acc, "PLN")
        rate = fx_rates.get(ccy, Decimal("1")) if ccy != "PLN" else Decimal("1")
        total += native * rate
    return max(total, Decimal(0))


# ── allocation computation ────────────────────────────────────────────────────

def compute_allocation(
    events: list[Event],
    prices: dict[str, "Quote"],
    fx_rates: dict[str, Decimal],
    config: "AppConfig",
    by: str,
    ex_cash: bool = False,
) -> AllocationResult:
    from portfolio_tracker.config import normalized_targets

    trade_events = [e for e in events if e.type == EventType.TRADE]
    positions = compute_positions(trade_events, prices=prices, fx_rates=fx_rates)

    # market value per ticker in PLN
    pos_value: dict[str, Decimal] = {}
    for pos in positions:
        if pos.market_value_pln is not None:
            pos_value[pos.symbol] = pos_value.get(pos.symbol, Decimal(0)) + pos.market_value_pln

    # resolve tags for each position
    name_by_symbol: dict[str, str | None] = {}
    for e in events:
        if e.instrument and e.instrument.symbol not in name_by_symbol:
            name_by_symbol[e.instrument.symbol] = e.instrument.name

    tags: dict[str, TaggedInstrument] = {}
    for pos in positions:
        name = name_by_symbol.get(pos.symbol)
        overrides = config.instruments.get(pos.symbol, {})
        tags[pos.symbol] = tag_instrument(pos.symbol, name, overrides)

    # accumulate into buckets
    buckets: dict[str, Decimal] = {}
    untagged_pln = Decimal(0)

    for ticker, value_pln in pos_value.items():
        tag = tags.get(ticker)
        if tag is None:
            untagged_pln += value_pln
            continue

        if by == "role":
            key = tag.role.value if tag.role is not None else None
        else:
            key = tag.asset_class.value if tag.asset_class is not None else None

        if key is None or tag.source == "untagged":
            untagged_pln += value_pln
            continue

        if ex_cash and key == "cash":
            continue

        buckets[key] = buckets.get(key, Decimal(0)) + value_pln

    # bank cash balance
    if not ex_cash:
        cash_total = _cash_pln(events, fx_rates)
        if cash_total > 0:
            buckets["cash"] = buckets.get("cash", Decimal(0)) + cash_total

    # extra-assets (real-assets, reserves) are excluded from the liquid allocation view
    total_pln = sum(buckets.values(), Decimal(0))

    # ensure every standard bucket appears (even if 0)
    if by == "role":
        for role in Role:
            if ex_cash and role == Role.CASH:
                continue
            if role.value not in buckets:
                buckets[role.value] = Decimal(0)
        norm_targets = normalized_targets(config)
    else:
        for ac in AssetClass:
            if ex_cash and ac == AssetClass.CASH:
                continue
            if ac.value not in buckets:
                buckets[ac.value] = Decimal(0)
        norm_targets = {}

    rows: list[AllocationRow] = []
    for bucket, value in sorted(buckets.items(), key=lambda x: -x[1]):
        weight = value / total_pln if total_pln > 0 else Decimal(0)
        if norm_targets:
            target = Decimal(str(norm_targets.get(bucket, 0.0)))
            drift = weight - target
            rebalance = (target - weight) * total_pln
        else:
            target = drift = rebalance = None
        rows.append(AllocationRow(
            bucket=bucket,
            value_pln=value,
            weight=weight,
            target=target,
            drift=drift,
            rebalance=rebalance,
        ))

    return AllocationResult(
        rows=rows,
        total_pln=total_pln,
        untagged_pln=untagged_pln,
        lens=by,
    )
