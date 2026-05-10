"""FastAPI entry point for the Stock Screener backend.

All stock data lives in Supabase Postgres (tables `fundamentals`, `daily_prices`).
The novice-intent translator handles the most common beginner phrasings
deterministically; the LLM Filter handles everything else.
"""

import asyncio
import json as _json
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import pandas as pd
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from db import (
    AsyncSessionLocal, get_db,
    SavedQuery, QueryHistory, ChatSession, UserEvent,
)
from models import intent as intent_module
from models import markets as markets_module
from models.explain import explain_results
from models.filter import Filter
from models.guard import GuardModel, SafetyLabel

load_dotenv()
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# Chat-agent logger — emits one line per LLM round and tool call so we can
# see where time goes and which calls fail. Format matches uvicorn's so the
# lines interleave cleanly in the dev console.
chat_log = logging.getLogger("chat_agent")
if not chat_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter(
        "%(asctime)s [chat] %(message)s", datefmt="%H:%M:%S",
    ))
    chat_log.addHandler(_h)
    chat_log.setLevel(logging.INFO)
    chat_log.propagate = False

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

# ── Chat agent ────────────────────────────────────────────────────────────────

_chat_client: Optional[AsyncOpenAI] = None

# Llama 3.3 70B is materially faster than Qwen 3.5 397B on NIM (~2-3x lower
# latency per round) and its tool-calling is reliable. We trade some
# multi-step reasoning depth for a much snappier chat — fine for the agent's
# job (definitions, news, comparisons, lookups).
CHAT_MODEL = "meta/llama-3.3-70b-instruct"

CHAT_SYSTEM_PROMPT = """You are the conversational stock-market companion built into a stock screener app. The screener page handles "find me stocks matching X" — your job is everything the screener can't: definitions, news, comparisons, deep dives, trends.

**What you're for**:
- **Definitions & education**: explaining financial terms in plain English
- **News & sentiment**: latest headlines, earnings, analyst takes (via `search_web` / Indian APIs)
- **Specific-stock deep dives**: fundamentals + context on a single ticker
- **Comparisons**: side-by-side analysis of 2-3 named stocks
- **Trends & sector pulse**: what's moving and why

**What you're NOT for**: criteria-based discovery ("safe stocks", "P/E < 15", "high-dividend tech"). Point those users to the screener page. DO NOT call `screen_stocks` for pure criteria queries — only when you genuinely need a small candidate set as input to a richer answer (e.g., "compare 3 Indian banks for me").

**Multi-market awareness**: User picks a market scope (Global / India / Nasdaq). Stay in scope. India → INR (₹), NSE/BSE; Nasdaq → USD ($); Global → currency per stock. If asked about a stock outside scope, mention it and ask before switching.

==============================================================
**HOW TO ANSWER** — follow this structure every time:

1. **LEAD WITH A CLEAR TAKE in the first sentence.** Even when nuanced, give a direct answer first, *then* the caveats. Never punt with "we need more data" — you have the data, use it.

2. **DEFINE EVERY FINANCIAL TERM** the first time it appears, inline in parentheses. No exceptions.

3. **BOLD THE KEY NUMBERS** with `**...**`. The user should be able to skim and get the answer.

4. **SHORT, CLICKABLE FOLLOW-UPS** — end with `*Want to know more?*` then 2-3 SHORT QUESTIONS (not research tasks) separated by ` · `. Phrase them as a beginner would ask, not as a syllabus.

==============================================================
**WORKED EXAMPLE** — user asks "Compare TCS and Infosys — which is more profitable?":

GOOD response:
> **TCS edges ahead** on raw profit margin, but Infosys wins on capital efficiency. Both are top-tier Indian IT names — there's no bad choice here.
>
> - **Profit margin** (% of revenue kept as profit after all costs): TCS **19.3%** vs Infosys **16.6%** — TCS wins.
> - **Return on equity** (profit generated per ₹ of shareholder money): Infosys **29.0%** vs TCS **24.2%** — Infosys wins.
>
> **What this means**: TCS is more efficient at turning sales into profit; Infosys gets more profit out of every rupee shareholders have put in. They're competing on different strengths.
>
> *Want to know more? Why is TCS's margin higher? · Which is safer for a beginner? · Which one pays better dividends?*

BAD response (do NOT do this):
> "Based on the data, TCS has a higher net profit margin (19.28%) than Infosys (16.62%). However, Infosys has higher return on average equity. To determine which is more profitable, we need to consider revenue, operating expenses, and cash flow. Follow-up suggestions: 1. Compare revenue growth. 2. Analyze operating expenses. 3. Examine cash flow statements."

Why it's bad: dodges the question, no definitions, no bold, follow-ups read like research homework.

==============================================================
**STYLE RULES**:
- Plain English first; jargon second (and defined inline)
- Bullets for any comparison; bold for key numbers
- Never recommend buying or selling — present what the data shows
- For Indian stocks, prefer `get_live_indian_stock` for live BSE/NSE data
- Use `search_web` liberally for recent news, earnings, sentiment — that's your edge over the static DB
- If a query is unclear, ask ONE clarifying question before calling tools
- NO emojis. Replies under 350 words unless asked for a deep dive

**Off-topic**: gently steer back — "I'm here to help with stocks and markets — anything investing-related I can help with?" """

CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "screen_stocks",
            "description": "Search for stocks matching financial criteria using natural language. Returns matching stocks with key metrics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language criteria, e.g. 'profitable tech companies with P/E under 20 and low debt'",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_info",
            "description": "Get detailed fundamental data for a specific stock ticker symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker symbol, e.g. AAPL, TSLA",
                    }
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sector_performance",
            "description": "Get average 1-month return for all market sectors — useful for understanding which sectors are performing well.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_live_indian_stock",
            "description": "Get live price, fundamentals, and technical data for an Indian stock listed on BSE or NSE. Use for any question about a specific Indian company.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {
                        "type": "string",
                        "description": "Indian company name, e.g. 'Reliance Industries', 'TCS', 'Infosys', 'HDFC Bank'",
                    }
                },
                "required": ["company_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trending_indian_stocks",
            "description": "Get currently trending and most active stocks on Indian markets (BSE/NSE). Use when asked about hot stocks, trending stocks, or what's moving in India.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_indian_stock_news",
            "description": "Get the latest news articles for an Indian company or general Indian market news.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {
                        "type": "string",
                        "description": "Company name, e.g. 'Infosys', 'Tata Motors'. Use 'market' for general Indian market news.",
                    }
                },
                "required": ["company_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_indian_ipo",
            "description": "Get information about upcoming and recently listed IPOs on Indian stock markets.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for latest financial news, analyst opinions, earnings reports, market sentiment, or any recent information about stocks and markets. Use this when the user asks about recent events, breaking news, or when you need up-to-date context beyond the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, e.g. 'AAPL Q4 2025 earnings results', 'Indian stock market outlook 2026', 'Tesla latest news'",
                    }
                },
                "required": ["query"],
            },
        },
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe(val: Any) -> Any:
    """Make a value JSON-safe (replace NaN/Inf with None, unwrap numeric types)."""
    if hasattr(val, "item"):
        val = val.item()
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    # Convert Decimals to float for JSON
    try:
        from decimal import Decimal
        if isinstance(val, Decimal):
            return float(val)
    except ImportError:
        pass
    return val


def _rows_to_dict(rows: list[dict], columns: list[str] | None = None) -> dict:
    """Shape DB rows into the {columns, rows, row_count} response contract."""
    if not rows:
        return {"columns": columns or [], "rows": [], "row_count": 0}
    cols = columns or list(rows[0].keys())
    out_rows = [[_safe(r.get(c)) for c in cols] for r in rows]
    return {"columns": cols, "rows": out_rows, "row_count": len(rows)}


async def _execute_select(sql: str, params: dict | list | None = None) -> list[dict]:
    """Run a SELECT through the async SQLAlchemy engine. Returns list of dicts."""
    async with AsyncSessionLocal() as db:
        # Convert positional $1/$2 style to dict if a list is passed
        bind = params if isinstance(params, dict) else {}
        if isinstance(params, list):
            # LLM filter uses $1, $2 — convert to :p1, :p2 for SQLAlchemy
            for i, val in enumerate(params, start=1):
                sql = sql.replace(f"${i}", f":p{i}")
                bind[f"p{i}"] = val
        elif isinstance(params, dict) and any(k.startswith("$") for k in params):
            for key, val in params.items():
                if key.startswith("$"):
                    idx = key[1:]
                    sql = sql.replace(f"${idx}", f":p{idx}")
                    bind[f"p{idx}"] = val
        result = await db.execute(text(sql), bind)
        return [dict(r) for r in result.mappings().all()]


# ── Chat tool dispatcher ──────────────────────────────────────────────────────


async def _execute_chat_tool(name: str, args: dict, market: str = "global") -> dict:
    """Execute a chat agent tool. Async — DB hits use the SQLAlchemy engine.

    `market` is the user's selected market scope, applied to screen_stocks
    so the chat agent's results respect the same dropdown selection.
    """
    if _filter is None:
        return {"error": "Backend not ready"}

    if name == "screen_stocks":
        query = args.get("query", "").strip()
        if not query:
            return {"error": "Empty query"}

        # Try novice-intent fast path first
        intent_res = intent_module.resolve(query, market=market)
        if intent_res:
            try:
                rows = await _execute_select(intent_res.sql, intent_res.params)
                return {
                    "intent":     intent_res.intent,
                    "explanation": intent_res.explanation,
                    "market":     intent_res.market,
                    "rows":       [{k: _safe(v) for k, v in r.items()} for r in rows[:15]],
                    "row_count":  len(rows),
                    "sql":        intent_res.sql,
                }
            except Exception as exc:
                return {"error": str(exc)}

        # Fall through to LLM
        sql_query = await _filter(query, market=market)
        if sql_query.error:
            return {"error": sql_query.error}
        try:
            rows = await _execute_select(sql_query.sql_template, sql_query.parameters)
            return {
                "rows":      [{k: _safe(v) for k, v in r.items()} for r in rows[:15]],
                "row_count": len(rows),
                "sql":       sql_query.sql_template,
            }
        except Exception as exc:
            return {"error": str(exc)}

    if name == "get_stock_info":
        ticker = args.get("ticker", "").upper().strip()
        if not ticker:
            return {"error": "No ticker provided"}
        try:
            rows = await _execute_select(
                "SELECT * FROM fundamentals WHERE ticker = :t LIMIT 1",
                {"t": ticker},
            )
            if not rows:
                return {"error": f"Ticker {ticker} not found in database"}
            return {k: _safe(v) for k, v in rows[0].items()}
        except Exception as exc:
            return {"error": str(exc)}

    if name == "get_sector_performance":
        try:
            rows = await _execute_select(
                """SELECT sector,
                          AVG(month_change) AS avg_1m_change,
                          COUNT(*) AS stock_count
                   FROM fundamentals
                   WHERE sector IS NOT NULL
                   GROUP BY sector
                   ORDER BY avg_1m_change DESC NULLS LAST"""
            )
            return {
                "sectors": [
                    {
                        "sector":        str(r["sector"]),
                        "avg_1m_change": _safe(r["avg_1m_change"]),
                        "stock_count":   int(r["stock_count"]),
                    }
                    for r in rows
                ]
            }
        except Exception as exc:
            return {"error": str(exc)}

    if name == "get_live_indian_stock":
        from services.indian_api import get_stock
        company = args.get("company_name", "").strip()
        if not company:
            return {"error": "No company name provided"}
        try:
            return get_stock(company)
        except Exception as exc:
            return {"error": str(exc)}

    if name == "get_trending_indian_stocks":
        from services.indian_api import get_trending
        try:
            return get_trending()
        except Exception as exc:
            return {"error": str(exc)}

    if name == "get_indian_stock_news":
        from services.indian_api import get_news
        company = args.get("company_name", "").strip()
        if not company:
            return {"error": "No company name provided"}
        try:
            return get_news(company)
        except Exception as exc:
            return {"error": str(exc)}

    if name == "get_indian_ipo":
        from services.indian_api import get_ipo
        try:
            return get_ipo()
        except Exception as exc:
            return {"error": str(exc)}

    if name == "search_web":
        from services.tavily_search import search_web as _tavily_search
        query = args.get("query", "").strip()
        if not query:
            return {"error": "Empty search query"}
        try:
            return _tavily_search(query, max_results=5)
        except Exception as exc:
            return {"error": f"Web search failed: {str(exc)}"}

    return {"error": f"Unknown tool: {name}"}


# ── Event logger (fire-and-forget) ────────────────────────────────────────────

async def _log_event(
    event_type: str,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Write a user_events row. Never raises — logging must not break main flow."""
    try:
        async with AsyncSessionLocal() as db:
            db.add(UserEvent(
                user_id=user_id,
                session_id=session_id,
                event_type=event_type,
                event_metadata=metadata,
            ))
            await db.commit()
    except Exception:
        pass


# ── Startup / shutdown ────────────────────────────────────────────────────────

_guard: Optional[GuardModel] = None
_filter: Optional[Filter] = None


async def _prewarm_chat_client() -> None:
    """Send a tiny request to NIM at startup so the first user query doesn't
    pay the cold-start cost (TLS handshake + model warm-up — typically 3-6s).
    Fire-and-forget; failures are logged but don't block startup.
    """
    if _chat_client is None:
        return
    t0 = time.perf_counter()
    try:
        await _chat_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        chat_log.info("prewarm ok took=%.2fs model=%s", time.perf_counter() - t0, CHAT_MODEL)
    except Exception as exc:
        chat_log.warning(
            "prewarm FAILED took=%.2fs %s: %s",
            time.perf_counter() - t0, type(exc).__name__, exc,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _guard, _filter, _chat_client
    _guard = GuardModel()
    _filter = Filter()
    if os.getenv("NVIDIA_NIM_API_KEY"):
        _chat_client = AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=os.getenv("NVIDIA_NIM_API_KEY"),
        )
    else:
        _chat_client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API"),
        )
    # Background prewarm — don't block startup waiting for the model to wake up.
    asyncio.create_task(_prewarm_chat_client())
    yield


app = FastAPI(title="Stock Screener API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.getenv("FRONTEND_URL", "http://localhost:3000"),
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth ──────────────────────────────────────────────────────────────────────


async def _ensure_user_row(user_id: str, email: str | None) -> None:
    """Mirror an auth.users row into the local `users` table so FK constraints
    on query_history / saved_queries / chat_sessions are satisfied.

    Auth is owned by Supabase Auth — the local row is purely a presence marker
    so the per-table FKs work. Idempotent via ON CONFLICT DO NOTHING.
    """
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO users (id, email, password_hash, created_at)
                    VALUES (:id, :email, :password_hash, now())
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id":            user_id,
                    "email":         email or f"{user_id}@supabase.local",
                    # Real auth is via Supabase JWT; this column is legacy.
                    "password_hash": "supabase-auth",
                },
            )
            await db.commit()
    except Exception as exc:
        # Best-effort: log but don't break the request — most FK failures will
        # surface clearly downstream if this ever fails to provision.
        chat_log.warning("ensure_user_row failed user=%s: %s", user_id, exc)


async def get_current_user(authorization: str = Header(None)) -> dict:
    """FastAPI dependency — verifies the Supabase access token via GoTrue API
    and ensures a local `users` row exists for the verified identity.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization[7:]
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": SUPABASE_ANON_KEY,
                },
            )
        if res.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = res.json()
        user_id = user["id"]
        email   = user.get("email")
        await _ensure_user_row(user_id, email)
        return {"id": user_id, "email": email}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Pydantic models ───────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    prompt: str
    market: Optional[str] = None     # 'global' | 'india' | 'nasdaq'


class SaveQueryRequest(BaseModel):
    name: str
    prompt: str
    sql: Optional[str] = None
    filters: Optional[list] = None
    query_type: str = "prompt"


class UpdateSavedRequest(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    sql: Optional[str] = None
    filters: Optional[list] = None
    query_type: Optional[str] = None


class StructuredFilter(BaseModel):
    column: str
    operator: str   # gt, lt, gte, lte, eq, neq
    value: Any


class StructuredQueryRequest(BaseModel):
    filters: list[StructuredFilter]
    order_by: Optional[str] = None
    order_dir: Optional[str] = "desc"
    limit: Optional[int] = 50


class HistoryRequest(BaseModel):
    prompt: str
    sql: Optional[str] = None
    row_count: Optional[int] = None


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    market: Optional[str] = None     # 'global' | 'india' | 'nasdaq'


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    """Simple health check — also keeps Supabase free tier from idle-pausing."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Markets + Presets ─────────────────────────────────────────────────────────


@app.get("/api/markets")
async def get_markets():
    """Return the list of selectable markets for the frontend dropdown."""
    return {
        "markets":  markets_module.public_metadata(),
        "default":  markets_module.DEFAULT_MARKET,
    }


# Market-aware presets. Each market gets a curated set tuned to its universe.
PRESETS_BY_MARKET: dict[str, list[dict]] = {
    "global": [
        {"id": "g-blue-chip", "label": "Global blue-chip leaders",     "intent": "blue_chip"},
        {"id": "g-growth",    "label": "High-growth companies",         "intent": "growth"},
        {"id": "g-value",     "label": "Cheap value plays",             "intent": "value"},
        {"id": "g-income",    "label": "High dividend yields",          "intent": "income"},
        {"id": "g-safe",      "label": "Safe & stable picks",           "intent": "safe"},
        {"id": "g-tech",      "label": "Growing tech worldwide",        "intent": "growth", "sector": "Technology"},
    ],
    "india": [
        {"id": "in-bluechip", "label": "Sensex / Nifty blue chips",     "intent": "blue_chip"},
        {"id": "in-safe",     "label": "Safe Nifty dividend stocks",    "intent": "safe"},
        {"id": "in-growth",   "label": "Fast-growing Indian companies", "intent": "growth"},
        {"id": "in-value",    "label": "Cheap Indian large-caps",       "intent": "value"},
        {"id": "in-tech",     "label": "Indian IT leaders",             "intent": "growth",    "sector": "Technology"},
        {"id": "in-bank",     "label": "Indian banking stocks",         "intent": "blue_chip", "sector": "Financial Services"},
    ],
    "nasdaq": [
        {"id": "nq-bigtech",  "label": "Nasdaq mega-cap tech",          "intent": "blue_chip", "sector": "Technology"},
        {"id": "nq-growth",   "label": "Fast-growing Nasdaq names",     "intent": "growth"},
        {"id": "nq-value",    "label": "Cheap Nasdaq stocks",           "intent": "value"},
        {"id": "nq-safe",     "label": "Safer Nasdaq picks",            "intent": "safe"},
        {"id": "nq-income",   "label": "Nasdaq dividend payers",        "intent": "income"},
        {"id": "nq-semi",     "label": "Semiconductor leaders",         "intent": "growth",    "sector": "Technology"},
    ],
}


@app.get("/api/presets")
async def get_presets(market: Optional[str] = None):
    """Return presets for the selected market (defaults to global)."""
    key = markets_module.normalize(market)
    return {"market": key, "presets": PRESETS_BY_MARKET.get(key, PRESETS_BY_MARKET["global"])}


# ── Core query endpoints ──────────────────────────────────────────────────────


def _clarify_response() -> dict:
    """Returned when the LLM rejects a vague query — turns the rejection into guidance."""
    return {
        "type": "clarify",
        "question": "What matters most to you?",
        "presets": [
            {"label": "Safe & stable",    "intent": "safe"},
            {"label": "Fast growth",      "intent": "growth"},
            {"label": "Cheap valuations", "intent": "value"},
            {"label": "Dividend income",  "intent": "income"},
        ],
    }


@app.post("/api/query")
async def run_query(req: QueryRequest):
    if _guard is None or _filter is None:
        raise HTTPException(status_code=503, detail="Models not loaded yet")

    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Empty prompt")

    market = markets_module.normalize(req.market)

    # Step 1: Safety
    label, categories = _guard(prompt)
    if label is not SafetyLabel.Safe:
        return {"error": "unsafe", "categories": list(categories)}

    # Step 2: Try novice intent fast path (no LLM)
    intent_res = intent_module.resolve(prompt, market=market)
    if intent_res:
        try:
            rows = await _execute_select(intent_res.sql, intent_res.params)
            result = _rows_to_dict(rows)
            result["sql"]                = intent_res.sql
            result["intent"]             = intent_res.intent
            result["intent_explanation"] = intent_res.explanation
            result["market"]             = intent_res.market
            asyncio.create_task(_log_event(
                "query_run",
                metadata={"prompt": prompt, "market": market, "intent": intent_res.intent, "row_count": result["row_count"]},
            ))
            return result
        except Exception as exc:
            return {"error": "sql_error", "detail": str(exc)}

    # Step 3: LLM filter pipeline (market injected via secondary system message)
    sql_query = await _filter(prompt, market=market)
    if sql_query.error:
        if sql_query.error in ("non-finance", "ambiguous"):
            return _clarify_response()
        return {"error": sql_query.error}

    try:
        rows = await _execute_select(sql_query.sql_template, sql_query.parameters)
        result = _rows_to_dict(rows)
        result["sql"]    = sql_query.sql_template
        result["market"] = market
        asyncio.create_task(_log_event(
            "query_run",
            metadata={"prompt": prompt, "market": market, "row_count": result["row_count"], "sql": sql_query.sql_template},
        ))
        return result
    except Exception as exc:
        return {"error": "sql_error", "detail": str(exc)}


@app.post("/api/query/stream")
async def run_query_stream(req: QueryRequest):
    """SSE — streams pipeline steps as they complete, plus per-row explanations."""
    if _guard is None or _filter is None:
        async def _unavailable():
            yield f"data: {_json.dumps({'error': 'server_error'})}\n\n"
        return StreamingResponse(_unavailable(), media_type="text/event-stream")

    prompt = req.prompt.strip()
    if not prompt:
        async def _empty():
            yield f"data: {_json.dumps({'error': 'empty'})}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    market = markets_module.normalize(req.market)

    async def generate():
        loop = asyncio.get_event_loop()

        # ── Safety ──────────────────────────────────────────────────────────
        yield f"data: {_json.dumps({'step': 'safety'})}\n\n"
        label, categories = await loop.run_in_executor(None, lambda: _guard(prompt))
        if label is not SafetyLabel.Safe:
            yield f"data: {_json.dumps({'error': 'unsafe', 'categories': list(categories)})}\n\n"
            return

        intent_explanation: str | None = None
        sql_used: str = ""
        rows: list[dict] = []

        # ── Novice intent fast path ─────────────────────────────────────────
        intent_res = intent_module.resolve(prompt, market=market)
        if intent_res:
            yield f"data: {_json.dumps({'step': 'executing', 'sql': intent_res.sql, 'intent': intent_res.intent, 'market': intent_res.market})}\n\n"
            try:
                rows = await _execute_select(intent_res.sql, intent_res.params)
                sql_used = intent_res.sql
                intent_explanation = intent_res.explanation
            except Exception as exc:
                yield f"data: {_json.dumps({'error': 'sql_error', 'detail': str(exc)})}\n\n"
                return
        else:
            # ── LLM filter ──────────────────────────────────────────────────
            yield f"data: {_json.dumps({'step': 'generating'})}\n\n"
            sql_query = await _filter(prompt, market=market)
            if sql_query.error:
                if sql_query.error in ("non-finance", "ambiguous"):
                    yield f"data: {_json.dumps({**_clarify_response(), 'step': 'done'})}\n\n"
                    return
                yield f"data: {_json.dumps({'error': sql_query.error})}\n\n"
                return
            yield f"data: {_json.dumps({'step': 'executing', 'sql': sql_query.sql_template, 'market': market})}\n\n"
            try:
                rows = await _execute_select(sql_query.sql_template, sql_query.parameters)
                sql_used = sql_query.sql_template
            except Exception as exc:
                yield f"data: {_json.dumps({'error': 'sql_error', 'detail': str(exc)})}\n\n"
                return

        # ── Send rows immediately ───────────────────────────────────────────
        result = _rows_to_dict(rows)
        result["sql"]    = sql_used
        result["market"] = market
        result["step"]   = "done"
        if intent_explanation:
            result["intent_explanation"] = intent_explanation
        yield f"data: {_json.dumps(result)}\n\n"

        asyncio.create_task(_log_event(
            "query_run",
            metadata={"prompt": prompt, "market": market, "row_count": result["row_count"], "sql": sql_used},
        ))

        # ── Per-row explanations (best-effort, post-rows) ───────────────────
        if rows:
            try:
                explanations = await explain_results(prompt, rows, intent_explanation)
                if explanations:
                    yield f"data: {_json.dumps({'step': 'explanations', 'explanations': explanations})}\n\n"
            except Exception:
                pass  # explanations are nice-to-have, never block the stream

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Stock detail ──────────────────────────────────────────────────────────────


@app.get("/api/stock/{ticker}")
async def get_stock(ticker: str):
    ticker = ticker.upper()
    try:
        fund_rows = await _execute_select(
            "SELECT * FROM fundamentals WHERE ticker = :t LIMIT 1",
            {"t": ticker},
        )
        if not fund_rows:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")

        fund_row = {k: _safe(v) for k, v in fund_rows[0].items()}

        prices: list[dict] = []
        technicals: dict = {}
        try:
            price_rows = await _execute_select(
                """SELECT date, open, high, low, close, volume
                   FROM daily_prices
                   WHERE ticker = :t
                   ORDER BY date DESC
                   LIMIT 1300""",
                {"t": ticker},
            )
            # Re-sort ascending for chart + indicator math
            price_rows = list(reversed(price_rows))
            prices = [{k: _safe(v) for k, v in row.items()} for row in price_rows]

            if len(price_rows) > 1:
                price_df = pd.DataFrame(price_rows)
                closes = price_df["close"].astype(float)
                volumes = price_df["volume"].astype(float)
                highs = price_df["high"].astype(float)
                lows = price_df["low"].astype(float)
                latest_close = float(closes.iloc[-1])

                technicals["week52High"] = _safe(float(highs.max()))
                technicals["week52Low"]  = _safe(float(lows.min()))

                for period in (20, 50, 200):
                    if len(closes) >= period:
                        sma = float(closes.iloc[-period:].mean())
                        technicals[f"sma{period}"] = _safe(round(sma, 2))

                if len(volumes) >= 20:
                    technicals["avgVolume20d"] = _safe(int(volumes.iloc[-20:].mean()))

                for label, days in (("1d", 1), ("1w", 5), ("1m", 21), ("3m", 63), ("6m", 126), ("1y", 252)):
                    if len(closes) > days:
                        prev = float(closes.iloc[-(days + 1)])
                        if prev:
                            technicals[f"change{label.upper()}"] = _safe(round((latest_close - prev) / prev, 4))

                if len(closes) >= 21:
                    daily_returns = closes.pct_change().dropna().iloc[-20:]
                    if len(daily_returns) > 0:
                        vol = float(daily_returns.std() * (252 ** 0.5))
                        technicals["volatility20d"] = _safe(round(vol, 4))

                if len(closes) >= 15:
                    deltas = closes.diff().iloc[-15:]
                    gains = deltas.where(deltas > 0, 0.0)
                    losses = (-deltas.where(deltas < 0, 0.0))
                    avg_gain = float(gains.mean())
                    avg_loss = float(losses.mean())
                    if avg_loss != 0:
                        rs = avg_gain / avg_loss
                        technicals["rsi14"] = _safe(round(100 - (100 / (1 + rs)), 2))

        except Exception:
            prices = []

        # Quarterly financials trend (for the detail-page mini chart).
        quarterly: list[dict] = []
        try:
            qrows = await _execute_select(
                """SELECT quarter_end, revenue, net_income, operating_inc, gross_profit
                   FROM quarterly_financials
                   WHERE ticker = :t
                   ORDER BY quarter_end ASC
                   LIMIT 16""",
                {"t": ticker},
            )
            quarterly = [{k: _safe(v) for k, v in row.items()} for row in qrows]
        except Exception:
            quarterly = []

        asyncio.create_task(_log_event("stock_viewed", metadata={"ticker": ticker}))
        return {
            "fundamentals": fund_row,
            "prices":       prices,
            "technicals":   technicals,
            "quarterly":    quarterly,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Sectors ───────────────────────────────────────────────────────────────────


@app.get("/api/sectors")
async def get_sectors():
    try:
        rows = await _execute_select(
            """SELECT DISTINCT ticker, sector, industry, market_cap, month_change
               FROM fundamentals
               WHERE sector IS NOT NULL AND industry IS NOT NULL"""
        )
        df = pd.DataFrame(rows)
        if df.empty:
            return {"dot": "digraph sectors {}", "sectors": [], "tickers": []}

        dot = _generate_dot(df)
        sector_df = (
            df.groupby("sector")["month_change"]
            .mean()
            .reset_index()
            .rename(columns={"month_change": "avgChange"})
        )
        sectors = [
            {"sector": str(r["sector"]), "avgChange": _safe(r["avgChange"])}
            for _, r in sector_df.iterrows()
        ]
        tickers_list = [
            {
                "ticker":     str(r["ticker"]),
                "sector":     str(r["sector"]),
                "industry":   str(r["industry"]),
                "marketCap":  _safe(r["market_cap"]),
                "monthChange": _safe(r["month_change"]),
            }
            for _, r in df.iterrows()
        ]
        return {"dot": dot, "sectors": sectors, "tickers": tickers_list}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _generate_dot(df: pd.DataFrame) -> str:
    lines = [
        "digraph sectors {",
        "  rankdir=LR;",
        '  node [shape=box, style=filled, fontname="Helvetica"];',
        '  graph [bgcolor="transparent"];',
    ]
    seen_edges: set[str] = set()

    for sector in df["sector"].dropna().unique():
        lines.append(
            f'  "{sector}" [fillcolor="#374151", fontcolor="white", fontsize="12", penwidth="0"];'
        )

    for _, row in df.iterrows():
        sector = str(row.get("sector") or "Unknown")
        industry = str(row.get("industry") or "Unknown")
        ticker = str(row.get("ticker") or "")
        market_cap = float(row.get("market_cap") or 0)
        month_change = float(row.get("month_change") or 0)

        fill = "#22c55e" if month_change > 0 else ("#ef4444" if month_change < 0 else "#6b7280")
        fontsize = max(8, min(16, int(8 + math.log10(max(market_cap, 1e6)) * 0.6)))

        edge_si = f"{sector}|{industry}"
        edge_it = f"{industry}|{ticker}"

        if edge_si not in seen_edges:
            seen_edges.add(edge_si)
            lines.append(f'  "{sector}" -> "{industry}";')
            lines.append(
                f'  "{industry}" [fillcolor="#1f2937", fontcolor="white", fontsize="10", penwidth="0"];'
            )

        if ticker and edge_it not in seen_edges:
            seen_edges.add(edge_it)
            lines.append(f'  "{industry}" -> "{ticker}";')
            lines.append(
                f'  "{ticker}" [fillcolor="{fill}", fontcolor="white", fontsize="{fontsize}", penwidth="0"];'
            )

    lines.append("}")
    return "\n".join(lines)


# ── All stocks browse endpoint ────────────────────────────────────────────────


@app.get("/api/stocks")
async def list_stocks(
    market: str = "global",
    q: str | None = None,
    sector: str | None = None,
    limit: int = 500,
):
    """Browse-all-stocks endpoint for the /stocks page.

    Scoped to the user's selected market. Optional `q` does a case-insensitive
    contains match on ticker / company / sector. Sorted by market cap DESC.
    """
    market_key = markets_module.normalize(market)
    where_parts: list[str] = []
    params: dict = {}

    market_clause, market_params = markets_module.filter_clause(market_key)
    if market_clause:
        where_parts.append(market_clause)
        params.update(market_params)

    if q:
        where_parts.append(
            "(LOWER(ticker) LIKE :q "
            "OR LOWER(company_name) LIKE :q "
            "OR LOWER(COALESCE(sector, '')) LIKE :q)"
        )
        params["q"] = f"%{q.strip().lower()}%"

    if sector:
        where_parts.append("sector = :sector")
        params["sector"] = sector

    where_sql = " AND ".join(where_parts) if where_parts else "1=1"
    sql = (
        "SELECT ticker, company_name, country, exchange, currency, sector, "
        "industry, market_cap, pe_ratio, dividend_yield, beta, eps, "
        "revenue_growth, profit_margin, debt_to_equity, return_on_equity, "
        "week52_high, week52_low, last_price, month_change, year_change "
        f"FROM fundamentals WHERE {where_sql} "
        "ORDER BY market_cap DESC NULLS LAST "
        f"LIMIT {int(limit)}"
    )
    rows = await _execute_select(sql, params)
    safe_rows = [{k: _safe(v) for k, v in r.items()} for r in rows]
    return {
        "rows":   safe_rows,
        "total":  len(safe_rows),
        "market": market_key,
    }


# ── Columns metadata (visual builder) ─────────────────────────────────────────


@app.get("/api/columns")
async def get_columns():
    from models.prompts import columns
    return {"columns": columns}


_OP_MAP = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<=", "eq": "=", "neq": "!="}


@app.get("/api/columns/meta")
async def get_columns_meta():
    """Return column metadata for the visual screener builder."""
    from models.prompts import COLUMN_TYPES

    meta = []
    for col, col_type in COLUMN_TYPES.items():
        entry: dict[str, Any] = {"name": col, "type": col_type}
        if col_type == "categorical" and col not in ("ticker", "company_name", "description"):
            try:
                rows = await _execute_select(
                    f"SELECT DISTINCT {col} AS v FROM fundamentals WHERE {col} IS NOT NULL ORDER BY {col}"
                )
                entry["values"] = [r["v"] for r in rows]
            except Exception:
                entry["values"] = []
        meta.append(entry)
    return {"columns": meta}


@app.post("/api/query/structured")
async def run_structured_query(req: StructuredQueryRequest):
    """Execute a structured filter query — deterministic SQL, no LLM."""
    from models.prompts import COLUMN_TYPES, columns as ALLOWED

    if not req.filters:
        raise HTTPException(status_code=400, detail="At least one filter is required")

    allowed_set = set(ALLOWED)
    clauses: list[str] = []
    params: dict[str, Any] = {}

    for i, f in enumerate(req.filters, start=1):
        if f.column not in allowed_set:
            raise HTTPException(status_code=400, detail=f"Unknown column: {f.column}")
        if f.operator not in _OP_MAP:
            raise HTTPException(status_code=400, detail=f"Unknown operator: {f.operator}")

        col_type = COLUMN_TYPES.get(f.column, "numeric")
        op_sql = _OP_MAP[f.operator]
        param_key = f"v{i}"

        if col_type == "categorical":
            if f.operator not in ("eq", "neq"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Categorical column {f.column} only supports 'eq' and 'neq'",
                )
            clauses.append(f"{f.column} {op_sql} :{param_key}")
            params[param_key] = str(f.value)
        else:
            try:
                params[param_key] = float(f.value)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Numeric value required for {f.column}, got: {f.value}",
                )
            clauses.append(f"{f.column} {op_sql} :{param_key}")

    where_sql = " AND ".join(clauses)
    sql = f"SELECT * FROM fundamentals WHERE {where_sql}"

    if req.order_by and req.order_by in allowed_set:
        direction = "ASC" if req.order_dir == "asc" else "DESC"
        sql += f" ORDER BY {req.order_by} {direction} NULLS LAST"

    limit = min(req.limit or 50, 500)
    sql += f" LIMIT {limit}"

    try:
        rows = await _execute_select(sql, params)
        result = _rows_to_dict(rows)
        result["sql"] = sql
        asyncio.create_task(_log_event(
            "structured_query",
            metadata={"filters": [f.model_dump() for f in req.filters], "row_count": result["row_count"]},
        ))
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Query failed: {exc}")


# ── Indian Stock Market API (live data) ──────────────────────────────────────


@app.get("/api/live/stock")
async def live_stock(name: str):
    from services.indian_api import get_stock
    try:
        return get_stock(name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/live/trending")
async def live_trending():
    from services.indian_api import get_trending
    try:
        return get_trending()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/live/historical")
async def live_historical(stock_name: str, period: str = "1M"):
    from services.indian_api import get_historical
    try:
        return get_historical(stock_name, period)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/live/news")
async def live_news(name: str):
    from services.indian_api import get_news
    try:
        return get_news(name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/live/ipo")
async def live_ipo():
    from services.indian_api import get_ipo
    try:
        return get_ipo()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    return current_user


# ── Saved queries ─────────────────────────────────────────────────────────────


@app.get("/api/saved")
async def get_saved(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(SavedQuery)
        .where(SavedQuery.user_id == current_user["id"])
        .order_by(SavedQuery.created_at.desc())
    )
    return [
        {
            "id": q.id, "name": q.name, "prompt": q.prompt, "sql": q.sql,
            "filters": getattr(q, "filters", None),
            "query_type": getattr(q, "query_type", "prompt"),
            "created_at": q.created_at.isoformat(),
            "updated_at": q.updated_at.isoformat() if getattr(q, "updated_at", None) else None,
        }
        for q in rows.all()
    ]


@app.post("/api/saved")
async def save_query(req: SaveQueryRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    entry = SavedQuery(
        user_id=current_user["id"], name=req.name, prompt=req.prompt,
        sql=req.sql, filters=req.filters, query_type=req.query_type,
    )
    db.add(entry)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        chat_log.error(
            "save_query FAILED user=%s %s: %s",
            current_user["id"], type(exc).__name__, exc,
        )
        # Surface a clean message to the client instead of a generic 500.
        raise HTTPException(
            status_code=400,
            detail=f"Could not save screen: {type(exc).__name__}",
        )
    await db.refresh(entry)
    return {
        "id": entry.id, "name": entry.name, "prompt": entry.prompt, "sql": entry.sql,
        "filters": entry.filters, "query_type": entry.query_type,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


@app.put("/api/saved/{saved_id}")
async def update_saved(saved_id: str, req: UpdateSavedRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    entry = await db.get(SavedQuery, saved_id)
    if not entry or entry.user_id != current_user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    if req.name is not None:        entry.name = req.name
    if req.prompt is not None:      entry.prompt = req.prompt
    if req.sql is not None:         entry.sql = req.sql
    if req.filters is not None:     entry.filters = req.filters
    if req.query_type is not None:  entry.query_type = req.query_type
    await db.commit()
    await db.refresh(entry)
    return {
        "id": entry.id, "name": entry.name, "prompt": entry.prompt, "sql": entry.sql,
        "filters": entry.filters, "query_type": entry.query_type,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


@app.delete("/api/saved/{saved_id}")
async def delete_saved(saved_id: str, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(
        delete(SavedQuery).where(SavedQuery.id == saved_id, SavedQuery.user_id == current_user["id"])
    )
    await db.commit()
    return {"ok": True}


# ── Query history ─────────────────────────────────────────────────────────────


@app.get("/api/history")
async def get_history(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(QueryHistory)
        .where(QueryHistory.user_id == current_user["id"])
        .order_by(QueryHistory.created_at.desc())
        .limit(50)
    )
    return [
        {"id": q.id, "prompt": q.prompt, "sql": q.sql, "row_count": q.row_count, "created_at": q.created_at.isoformat()}
        for q in rows.all()
    ]


@app.post("/api/history")
async def add_history(req: HistoryRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    entry = QueryHistory(user_id=current_user["id"], prompt=req.prompt, sql=req.sql, row_count=req.row_count)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {"id": entry.id, "prompt": entry.prompt, "sql": entry.sql, "row_count": entry.row_count, "created_at": entry.created_at.isoformat()}


# ── Chat agent ────────────────────────────────────────────────────────────────


def _filter_think_tokens(text: str, state: dict) -> str:
    """Strip <think>…</think> blocks from a streaming token. `state` persists across chunks."""
    out = []
    i = 0
    while i < len(text):
        if not state["in_think"]:
            start = text.find("<think>", i)
            if start == -1:
                out.append(text[i:])
                break
            out.append(text[i:start])
            state["in_think"] = True
            i = start + 7
        else:
            end = text.find("</think>", i)
            if end == -1:
                break
            state["in_think"] = False
            i = end + 8
    return "".join(out)


TOOL_STATUS_LABELS = {
    "screen_stocks":              "Screening stocks…",
    "get_stock_info":             "Looking up stock data…",
    "get_sector_performance":     "Fetching sector data…",
    "get_live_indian_stock":      "Fetching live Indian stock data…",
    "get_trending_indian_stocks": "Loading trending Indian stocks…",
    "get_indian_stock_news":      "Pulling latest market news…",
    "get_indian_ipo":             "Fetching IPO listings…",
    "search_web":                 "Searching the web…",
}


# Plain-English labels for novice intents — used by the chat fast path narrative.
INTENT_LABELS = {
    "safe":      "safe",
    "growth":    "growth",
    "value":     "value",
    "income":    "high-dividend",
    "blue_chip": "blue-chip",
}


# Screener-card column order for screener_results SSE events. Matches the
# fields the frontend ResultsCards/StockCard reads.
_SCREENER_COLUMN_ORDER = (
    "ticker", "company_name", "exchange", "currency", "sector",
    "market_cap", "pe_ratio", "dividend_yield", "month_change",
    "year_change", "match_score",
)


def _shape_for_screener(rows: list[dict]) -> dict:
    """Convert dict-rows from the screener tool into the {columns, rows}
    shape the frontend ResultsCards component expects."""
    if not rows:
        return {"columns": [], "rows": []}
    all_keys: set = set()
    for r in rows:
        all_keys.update(r.keys())
    cols = [c for c in _SCREENER_COLUMN_ORDER if c in all_keys]
    cols += [c for c in all_keys if c not in cols]
    out_rows = [[_safe(r.get(c)) for c in cols] for r in rows]
    return {"columns": cols, "rows": out_rows}


# Phrases that signal a conversational ask (explanation/comparison) — those
# should go to the LLM, not the deterministic screener.
_CHAT_FASTPATH_SKIP = (
    "explain", "what is", "what's", "what are", "why ", "how ",
    "compare", " vs ", "difference", "tell me about",
)


def _should_chat_fastpath(message: str) -> bool:
    """Decide if a chat message is a clean screener-shaped query that should
    bypass the LLM. Triggers only on short, intent-matching messages with no
    conversational markers."""
    msg = message.strip()
    if not msg or len(msg) > 80:
        return False
    msg_low = msg.lower()
    if any(phrase in msg_low for phrase in _CHAT_FASTPATH_SKIP):
        return False
    return intent_module.detect_intent(msg) is not None


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    if _chat_client is None or _filter is None:
        raise HTTPException(status_code=503, detail="Backend not ready")

    session_id = req.session_id or str(uuid.uuid4())
    market = markets_module.normalize(req.market)

    history: list = []
    try:
        async with AsyncSessionLocal() as db:
            db_session = await db.get(ChatSession, session_id)
            history = db_session.messages if db_session else []
    except Exception:
        pass

    async def _save_session(updated_messages: list) -> None:
        try:
            async with AsyncSessionLocal() as s:
                existing = await s.get(ChatSession, session_id)
                if existing:
                    existing.messages   = updated_messages
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    s.add(ChatSession(id=session_id, messages=updated_messages))
                await s.commit()
        except Exception:
            pass

    # Short request id — last 6 chars of the session id is enough to grep on.
    rid = session_id[-6:]
    req_t0 = time.perf_counter()
    chat_log.info(
        "req=%s start market=%s msg_len=%d model=%s",
        rid, market, len(req.message or ""), CHAT_MODEL,
    )

    async def generate():
        nonlocal req_t0

        # ── Screener handoff ──────────────────────────────────────────────────
        # Criteria-based stock discovery ("safe stocks", "high dividend stocks")
        # is the screener's job, not the agent's. When the user asks one of
        # those in chat, hand off with a small inline CTA card instead of
        # invoking the LLM — clearer division of labor, instant response.
        if _should_chat_fastpath(req.message):
            intent_res = intent_module.resolve(req.message, market=market)
            if intent_res:
                label_text   = INTENT_LABELS.get(intent_res.intent, intent_res.intent)
                market_label = markets_module.MARKETS[intent_res.market].label
                yield (
                    "data: "
                    + _json.dumps({
                        "type":         "screener_handoff",
                        "intent":       intent_res.intent,
                        "intent_label": label_text,
                        "market":       intent_res.market,
                        "market_label": market_label,
                        "explanation":  intent_res.explanation,
                    })
                    + "\n\n"
                )
                # Brief context note in history — keeps future turns coherent
                # without saving a fake LLM response.
                updated = history + [
                    {"role": "user",      "content": req.message},
                    {
                        "role":    "assistant",
                        "content": (
                            f"(Handed off to screener for {label_text} stocks "
                            f"in {market_label}.)"
                        ),
                    },
                ]
                asyncio.create_task(_save_session(updated[-40:]))
                chat_log.info(
                    "req=%s done handoff intent=%s total=%.2fs",
                    rid, intent_res.intent, time.perf_counter() - req_t0,
                )
                yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id})}\n\n"
                return

        trimmed_history = []
        for msg in history[-40:]:
            c = msg.get("content", "")
            if isinstance(c, str) and len(c) > 8000:
                trimmed_history.append({**msg, "content": c[:8000] + "\n...(truncated)"})
            else:
                trimmed_history.append(msg)

        market_spec = markets_module.MARKETS[market]
        market_context = (
            f"User's selected market scope: **{market_spec.label}** "
            f"(currency: {market_spec.currency}, exchanges: "
            f"{', '.join(market_spec.exchanges) if market_spec.exchanges else 'all'}). "
            f"All screening tool calls are scoped to this market automatically."
        )

        messages = (
            [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
            + [{"role": "system", "content": market_context}]
            + trimmed_history
            + [{"role": "user", "content": req.message}]
        )

        tool_rounds   = 0
        total_tools   = 0
        while tool_rounds < 5:
            # ── LLM round (tool-routing call) ────────────────────────────────
            # NIM's Llama 3.3 70B endpoint rejects assistant messages that
            # contain >1 tool_call ("This model only supports single
            # tool-calls at once"). parallel_tool_calls=False tells the model
            # to emit one tool at a time; the loop iterates if it needs more.
            llm_t0 = time.perf_counter()
            try:
                tool_resp = await _chat_client.chat.completions.create(
                    model=CHAT_MODEL,
                    messages=messages,
                    tools=CHAT_TOOLS,
                    tool_choice="auto",
                    parallel_tool_calls=False,
                    max_tokens=512,
                )
            except Exception as exc:
                dt = time.perf_counter() - llm_t0
                chat_log.error(
                    "req=%s round=%d llm FAILED took=%.2fs %s: %s",
                    rid, tool_rounds + 1, dt, type(exc).__name__, exc,
                )
                yield (
                    "data: "
                    + _json.dumps({
                        "type":    "error",
                        "message": "The model request failed. Please try again.",
                        "detail":  f"{type(exc).__name__}: {exc}",
                    })
                    + "\n\n"
                )
                yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id})}\n\n"
                return

            asst    = tool_resp.choices[0].message
            llm_dt  = time.perf_counter() - llm_t0

            # Belt-and-suspenders for the NIM Llama 3.3 70B "single tool-call"
            # constraint. If parallel_tool_calls=False is ever ignored or the
            # model misbehaves, truncate to the first one and let the loop
            # pick up the rest on the next round.
            tool_calls = list(asst.tool_calls or [])
            if len(tool_calls) > 1:
                chat_log.warning(
                    "req=%s round=%d model emitted %d tool_calls; truncating to 1",
                    rid, tool_rounds + 1, len(tool_calls),
                )
                tool_calls = tool_calls[:1]

            n_calls = len(tool_calls)
            chat_log.info(
                "req=%s round=%d llm ok took=%.2fs tool_calls=%d",
                rid, tool_rounds + 1, llm_dt, n_calls,
            )

            if not tool_calls:
                content = (asst.content or "").strip()
                think_state = {"in_think": False}
                content = _filter_think_tokens(content, think_state)
                words = content.split(" ")
                for i, word in enumerate(words):
                    chunk = word + ("" if i == len(words) - 1 else " ")
                    yield f"data: {_json.dumps({'type': 'token', 'text': chunk})}\n\n"
                    await asyncio.sleep(0.018)

                updated = history + [
                    {"role": "user",      "content": req.message},
                    {"role": "assistant", "content": content},
                ]
                asyncio.create_task(_save_session(updated[-40:]))
                chat_log.info(
                    "req=%s done direct rounds=%d tools=%d total=%.2fs",
                    rid, tool_rounds + 1, total_tools,
                    time.perf_counter() - req_t0,
                )
                yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id})}\n\n"
                return

            messages.append({
                "role": "assistant",
                "content": asst.content or "",
                "tool_calls": [
                    {
                        "id":   tc.id,
                        "type": "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            # Emit status pill for every tool call up front, then dispatch.
            # With parallel_tool_calls=False this list is at most 1 entry on
            # this NIM model — the asyncio.gather still works fine for n=1.
            parsed_calls: list[tuple[Any, dict]] = []
            for tc in tool_calls:
                t_name = tc.function.name
                t_args = _json.loads(tc.function.arguments or "{}")
                parsed_calls.append((tc, t_args))
                label = TOOL_STATUS_LABELS.get(t_name, "Working…")
                yield f"data: {_json.dumps({'type': 'tool_call', 'label': label})}\n\n"

            tools_t0 = time.perf_counter()
            results  = await asyncio.gather(*[
                _execute_chat_tool(tc.function.name, t_args, market=market)
                for tc, t_args in parsed_calls
            ], return_exceptions=True)
            tools_dt = time.perf_counter() - tools_t0
            total_tools += len(parsed_calls)

            for (tc, _t_args), result in zip(parsed_calls, results):
                if isinstance(result, Exception):
                    chat_log.error(
                        "req=%s round=%d tool=%s FAILED %s: %s",
                        rid, tool_rounds + 1, tc.function.name,
                        type(result).__name__, result,
                    )
                    result = {"error": f"{type(result).__name__}: {result}"}
                else:
                    has_err = isinstance(result, dict) and result.get("error")
                    chat_log.info(
                        "req=%s round=%d tool=%s %s",
                        rid, tool_rounds + 1, tc.function.name,
                        f"err={result['error']!r}" if has_err else "ok",
                    )

                # When the agent screens stocks, surface the rows to the UI
                # as a screener_results event so the user sees real cards
                # (not just the LLM's prose summary).
                if (
                    tc.function.name == "screen_stocks"
                    and isinstance(result, dict)
                    and result.get("rows")
                ):
                    shaped = _shape_for_screener(result["rows"])
                    yield (
                        "data: "
                        + _json.dumps({
                            "type":    "screener_results",
                            "columns": shaped["columns"],
                            "rows":    shaped["rows"],
                            "intent":  result.get("intent"),
                            "market":  result.get("market", market),
                        })
                        + "\n\n"
                    )

                result_str = _json.dumps(result, default=str)
                if len(result_str) > 12000:
                    result_str = result_str[:12000] + '..."}'
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      result_str,
                })

            chat_log.info(
                "req=%s round=%d tools_done count=%d wallclock=%.2fs",
                rid, tool_rounds + 1, len(parsed_calls), tools_dt,
            )
            tool_rounds += 1

        # ── Streaming final answer ────────────────────────────────────────────
        # System prompt caps replies at ~350 words (~500 tokens). 1024 leaves
        # plenty of headroom for tables/citations without burning budget on
        # runaway responses.
        stream_t0 = time.perf_counter()
        try:
            stream = await _chat_client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                stream=True,
                max_tokens=1024,
            )
        except Exception as exc:
            chat_log.error(
                "req=%s final-stream FAILED %s: %s",
                rid, type(exc).__name__, exc,
            )
            yield (
                "data: "
                + _json.dumps({
                    "type":    "error",
                    "message": "The model request failed. Please try again.",
                    "detail":  f"{type(exc).__name__}: {exc}",
                })
                + "\n\n"
            )
            yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id})}\n\n"
            return

        full_response = ""
        think_state   = {"in_think": False}

        try:
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if not delta:
                    continue
                clean = _filter_think_tokens(delta, think_state)
                if clean:
                    full_response += clean
                    yield f"data: {_json.dumps({'type': 'token', 'text': clean})}\n\n"
        except Exception as exc:
            chat_log.error(
                "req=%s stream-iter FAILED after %d chars %s: %s",
                rid, len(full_response), type(exc).__name__, exc,
            )

        stream_dt = time.perf_counter() - stream_t0
        chat_log.info(
            "req=%s stream done chars=%d took=%.2fs",
            rid, len(full_response), stream_dt,
        )

        updated = history + [
            {"role": "user",      "content": req.message},
            {"role": "assistant", "content": full_response},
        ]
        asyncio.create_task(_save_session(updated[-40:]))
        chat_log.info(
            "req=%s done streaming rounds=%d tools=%d total=%.2fs",
            rid, tool_rounds, total_tools, time.perf_counter() - req_t0,
        )
        yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )
