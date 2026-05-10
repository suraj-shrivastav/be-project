"""Novice-intent translator.

Maps vague beginner language ("safe stocks", "growing companies") into
deterministic SQL — no LLM call needed for the common cases.

This is the mission-critical layer that lets a non-trader use the screener.
Market scoping is layered on top: the same intent can run against india,
nasdaq, or global depending on the user's selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .markets import filter_clause, normalize as normalize_market


@dataclass
class IntentResult:
    """A resolved novice intent — ready to execute."""

    intent: str          # canonical key, e.g. "safe"
    sql: str             # full SELECT … with named bind params
    params: dict         # bound parameter values for SQLAlchemy text()
    explanation: str     # one-line plain-English description of the intent
    market: str          # canonical market key applied to the query


# Each intent has:
#   filters:     a WHERE clause expression (NULL-safe via COALESCE/NULLIF)
#   ranking:     SQL expression scored higher = better match
#   explanation: shown to the user as "what this intent means"
INTENT_MAP: dict[str, dict[str, str]] = {
    "safe": {
        "filters": (
            "COALESCE(beta, 1.0) < 1.0 "
            "AND COALESCE(market_cap, 0) > 10000000000 "
            "AND COALESCE(dividend_yield, 0) > 0.01"
        ),
        "ranking": (
            "(market_cap / 1e9) "
            "+ (COALESCE(dividend_yield, 0) * 100) "
            "- (COALESCE(beta, 1.0) * 5)"
        ),
        "explanation": "low volatility, established companies, steady dividends",
    },
    "growth": {
        "filters": (
            "COALESCE(revenue_growth, 0) > 0.10 "
            "AND COALESCE(profit_margin, 0) > 0"
        ),
        "ranking": (
            "(COALESCE(revenue_growth, 0) * 100) "
            "+ (COALESCE(profit_margin, 0) * 50)"
        ),
        "explanation": "fast revenue growth with positive profit margins",
    },
    "value": {
        "filters": (
            "pe_ratio > 0 AND pe_ratio < 15 "
            "AND COALESCE(pb_ratio, 99) < 2"
        ),
        "ranking": (
            "(1.0 / NULLIF(pe_ratio, 0)) * 100 "
            "+ COALESCE(dividend_yield, 0) * 50"
        ),
        "explanation": "trading below typical valuation multiples",
    },
    "income": {
        "filters": "COALESCE(dividend_yield, 0) > 0.03",
        "ranking": (
            "COALESCE(dividend_yield, 0) * 100 "
            "+ LN(GREATEST(COALESCE(market_cap, 1), 1))"
        ),
        "explanation": "high dividend yields from stable payers",
    },
    "blue_chip": {
        "filters": (
            "COALESCE(market_cap, 0) > 50000000000 "
            "AND COALESCE(dividend_yield, 0) > 0"
        ),
        "ranking": "LN(GREATEST(COALESCE(market_cap, 1), 1))",
        "explanation": "the largest, most established companies",
    },
}

# Keyword groups — order matters: more specific intents first.
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("blue_chip", ("blue chip", "blue-chip", "large cap", "largecap", "mega cap", "sensex", "nifty 50")),
    ("income",    ("dividend", "income", "yield", "passive")),
    ("safe",      ("safe", "stable", "low risk", "low-risk", "retirement", "conservative", "defensive", "psu")),
    ("growth",    ("growth", "growing", "fast-growing", "expanding", "high growth", "midcap", "small cap")),
    ("value",     ("cheap", "undervalued", "value", "bargain")),
]


def detect_intent(prompt: str) -> Optional[str]:
    """Return a canonical intent key, or None if no novice phrasing matched."""
    p = prompt.lower()
    for intent, words in _KEYWORDS:
        if any(w in p for w in words):
            return intent
    return None


def detect_sector(prompt: str) -> Optional[str]:
    """Detect a sector hint. Conservative — only common ones."""
    p = prompt.lower()
    if "tech" in p or "technology" in p or "software" in p:
        return "Technology"
    if "health" in p or "pharma" in p or "biotech" in p:
        return "Healthcare"
    if "financ" in p or "bank" in p or "insurance" in p:
        return "Financial Services"
    if "energy" in p or "oil" in p or "gas" in p:
        return "Energy"
    if "consumer" in p:
        return "Consumer Cyclical"
    return None


def build_intent_query(
    intent: str,
    market: str = "global",
    sector: Optional[str] = None,
    limit: int = 20,
) -> IntentResult:
    """Build a fully-formed SQL query for the given intent + market scope.

    Returns SQL with named bind params ready for SQLAlchemy `text(sql)` execution.
    """
    market_key = normalize_market(market)
    template = INTENT_MAP[intent]

    where_parts = [template["filters"]]
    params: dict = {}

    market_clause, market_params = filter_clause(market_key)
    if market_clause:
        where_parts.append(market_clause)
        params.update(market_params)

    if sector:
        where_parts.append("sector = :sector")
        params["sector"] = sector

    where_clause = " AND ".join(where_parts)
    sql = (
        f"SELECT *, ({template['ranking']}) AS match_score "
        f"FROM fundamentals "
        f"WHERE {where_clause} "
        f"ORDER BY match_score DESC NULLS LAST "
        f"LIMIT {int(limit)}"
    )

    return IntentResult(
        intent=intent,
        sql=sql,
        params=params,
        explanation=template["explanation"],
        market=market_key,
    )


def resolve(prompt: str, market: str = "global") -> Optional[IntentResult]:
    """One-shot: detect intent + sector from the prompt and build the query.

    `market` is taken from the request (frontend selection) — we do NOT
    auto-detect market from the prompt to keep behavior predictable.

    Returns None if no novice intent was detected — caller should fall
    through to the LLM filter pipeline.
    """
    intent = detect_intent(prompt)
    if not intent:
        return None
    return build_intent_query(
        intent,
        market=market,
        sector=detect_sector(prompt),
    )
