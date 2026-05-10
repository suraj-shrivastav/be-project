"""Market scope definitions.

A "market" is a user-facing scope that maps to a set of exchanges. The frontend
sends `market` with each query; we translate to a SQL exchange filter here.

Adding a new market (e.g. "lse" for London) is a one-line change in MARKETS.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketSpec:
    key: str                 # canonical id used by frontend + API
    label: str               # human-facing name
    exchanges: tuple[str, ...]   # rows where exchange ∈ this set
    currency: str            # primary currency for display ('mixed' for Global)
    currency_symbol: str     # for the dropdown badge / row chips
    description: str         # one-line subtitle in the dropdown


MARKETS: dict[str, MarketSpec] = {
    "global": MarketSpec(
        key="global",
        label="Global",
        exchanges=(),                # empty = no filter
        currency="mixed",
        currency_symbol="¤",    # generic ¤ — frontend swaps per-row
        description="All listed markets",
    ),
    "india": MarketSpec(
        key="india",
        label="India",
        exchanges=("NSE", "BSE"),
        currency="INR",
        currency_symbol="₹",    # ₹
        description="NSE and BSE listings",
    ),
    "nasdaq": MarketSpec(
        # Key kept as "nasdaq" for backward compat with localStorage; the
        # scope now covers both NASDAQ and NYSE so the user-facing label is
        # the broader "US".
        key="nasdaq",
        label="US",
        exchanges=("NASDAQ", "NYSE"),
        currency="USD",
        currency_symbol="$",
        description="US-listed companies (Nasdaq + NYSE)",
    ),
}

DEFAULT_MARKET = "global"


def normalize(market: str | None) -> str:
    """Coerce an incoming market value to a known key — defaults to global."""
    if not market:
        return DEFAULT_MARKET
    key = market.lower().strip()
    return key if key in MARKETS else DEFAULT_MARKET


def filter_clause(market: str, alias: str = "") -> tuple[str, dict]:
    """Build a Postgres WHERE-clause fragment for the given market.

    Returns ("", {}) for global (no filter). Otherwise an `IN (...)` clause
    with bound parameters so we can compose into other queries safely.

    `alias` lets callers prefix the column name (e.g. 'f.exchange').
    """
    spec = MARKETS[normalize(market)]
    if not spec.exchanges:
        return "", {}

    col = f"{alias + '.' if alias else ''}exchange"
    placeholders = ", ".join(f":mkt_ex_{i}" for i in range(len(spec.exchanges)))
    params = {f"mkt_ex_{i}": ex for i, ex in enumerate(spec.exchanges)}
    return f"{col} IN ({placeholders})", params


def llm_hint(market: str) -> str:
    """Sentence injected into the LLM system prompt to scope generated SQL."""
    spec = MARKETS[normalize(market)]
    if not spec.exchanges:
        return "No market filter applied — the user is browsing all markets."
    ex_list = ", ".join(f"'{e}'" for e in spec.exchanges)
    return (
        f"The user has selected the {spec.label} market. "
        f'Always include `"exchange" IN ({ex_list})` in the WHERE clause '
        f"so only {spec.label} stocks are returned."
    )


def public_metadata() -> list[dict]:
    """Shape used by the GET /api/markets endpoint for the frontend dropdown."""
    return [
        {
            "key":             m.key,
            "label":           m.label,
            "description":     m.description,
            "currency":        m.currency,
            "currency_symbol": m.currency_symbol,
            "exchanges":       list(m.exchanges),
        }
        for m in MARKETS.values()
    ]
