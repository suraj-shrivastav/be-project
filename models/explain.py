"""Per-row narrative explainer.

Given the user's prompt and the top stock matches, returns a one-line
"why this fits" sentence per ticker. Streamed alongside the rows so the
table appears instantly and explanations fade in afterwards.

Uses the same NIM Llama 3.3 70B client as the Filter — fast, cheap, JSON mode.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from openai import AsyncOpenAI

EXPLAIN_MODEL = "meta/llama-3.3-70b-instruct"
EXPLAIN_TIMEOUT = 8

EXPLAIN_SYSTEM = """You are a stock analyst writing one-line summaries.

For each stock provided, write ONE sentence (under 20 words, plain language) explaining why it matches the user's request.

Rules:
- Reference concrete metrics from the stock data (market cap, P/E, dividend yield, growth rate, etc.).
- Use plain English — a beginner should understand without finance background.
- No buy/sell recommendations.
- No emojis, no markdown, no preamble.
- Output JSON only: {"<ticker>": "<sentence>", ...}
"""


_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is not None:
        return _client
    if os.getenv("NVIDIA_NIM_API_KEY"):
        _client = AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=os.environ["NVIDIA_NIM_API_KEY"],
        )
    else:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ.get("OPENROUTER_API"),
        )
    return _client


def _trim_row(row: dict) -> dict:
    """Strip a row down to the fields the explainer cares about — saves tokens."""
    keep = (
        "ticker", "company_name", "sector", "industry",
        "market_cap", "pe_ratio", "dividend_yield", "beta",
        "revenue_growth", "profit_margin", "year_change", "month_change",
    )
    return {k: row.get(k) for k in keep if row.get(k) is not None}


async def explain_results(
    prompt: str,
    rows: list[dict],
    intent_explanation: str | None = None,
    max_rows: int = 5,
) -> dict[str, str]:
    """Return {ticker: one-line explanation} for the top rows.

    Returns an empty dict on any failure — explanations are nice-to-have,
    they must never block the rows from reaching the user.
    """
    if not rows:
        return {}

    top = [_trim_row(r) for r in rows[:max_rows]]
    intent_hint = f"\nThe user's intent was: {intent_explanation}" if intent_explanation else ""

    user_msg = (
        f'User asked: "{prompt}"{intent_hint}\n\n'
        f"Stocks to explain:\n{json.dumps(top, default=str)}\n\n"
        'Output JSON: {"<ticker>": "<one-line reason>", ...}'
    )

    try:
        client = _get_client()
        completion = await client.chat.completions.create(
            model=EXPLAIN_MODEL,
            messages=[
                {"role": "system", "content": EXPLAIN_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            max_completion_tokens=512,
            temperature=0.2,
            response_format={"type": "json_object"},
            timeout=EXPLAIN_TIMEOUT,
        )
        raw = completion.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        # Coerce values to strings, filter to known tickers
        wanted = {r["ticker"] for r in top if r.get("ticker")}
        return {
            str(k): str(v).strip()
            for k, v in parsed.items()
            if k in wanted and isinstance(v, (str, int, float))
        }
    except Exception:
        return {}
