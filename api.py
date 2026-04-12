"""FastAPI entry point for the Stock Screener backend."""

import asyncio
import json as _json
import math
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import duckdb
import pandas as pd
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db import (
    AsyncSessionLocal, get_db,
    SavedQuery, QueryHistory, ChatSession, UserEvent,
)
from models.filter import Filter
from models.guard import GuardModel, SafetyLabel

load_dotenv()
os.environ["TOKENIZERS_PARALLELISM"] = "false"

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

# ── Chat agent ────────────────────────────────────────────────────────────────

_chat_client: Optional[AsyncOpenAI] = None

CHAT_MODEL = "qwen/qwen3-14b"

CHAT_SYSTEM_PROMPT = """You are a stock market research assistant built into a stock screener platform.

You ONLY help with questions about:
- Stocks, shares, and equity markets (US, Indian BSE/NSE, and global)
- Financial metrics (P/E ratio, market cap, EPS, beta, dividends, etc.)
- Company fundamentals and financial analysis
- Sector and industry analysis
- Stock screening and filtering criteria
- Investment concepts and financial literacy
- Indian market data: live quotes, trending stocks, IPOs, and company news

If asked about ANYTHING else (sports, cooking, general knowledge, weather, politics, etc.),
respond with exactly: "I'm a stock market specialist — ask me about stocks, companies, or financial metrics!"

Rules:
- Explain financial terms in plain, simple language
- When showing data, always add context (e.g. "this P/E is below the sector average")
- Never say "you should buy/sell X" — describe what the data shows instead
- Keep answers concise; use bullet points for lists
- If the user's intent is unclear, ask one clarifying question before calling a tool
- When you call a tool, summarise the results in plain English — don't just dump raw numbers
- For Indian stocks, use get_live_indian_stock to fetch real-time BSE/NSE data
- NEVER use emojis in your responses — no icons, no emoticons, keep it clean text only"""

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
]


def _execute_chat_tool(name: str, args: dict) -> dict:
    """Execute a chat agent tool synchronously (runs in thread executor)."""
    if _filter is None or _conn is None:
        return {"error": "Backend not ready"}

    if name == "screen_stocks":
        query = args.get("query", "").strip()
        if not query:
            return {"error": "Empty query"}
        sql_query = _filter(query)
        if sql_query.error:
            return {"error": sql_query.error}
        try:
            df = _conn.execute(
                sql_query.sql_template, list(sql_query.parameters.values())
            ).fetchdf()
            result = _df_to_dict(df)
            result["rows"] = result["rows"][:15]   # cap rows sent to LLM
            result["row_count"] = len(df)
            result["sql"] = sql_query.sql_template
            return result
        except Exception as exc:
            return {"error": str(exc)}

    if name == "get_stock_info":
        ticker = args.get("ticker", "").upper().strip()
        if not ticker:
            return {"error": "No ticker provided"}
        try:
            df = _conn.execute(
                'SELECT * FROM fundamentals WHERE "Ticker" = ? LIMIT 1', [ticker]
            ).fetchdf()
            if df.empty:
                return {"error": f"Ticker {ticker} not found in database"}
            return {k: _safe(v) for k, v in df.iloc[0].to_dict().items()}
        except Exception as exc:
            return {"error": str(exc)}

    if name == "get_sector_performance":
        try:
            df = _conn.execute(
                """SELECT "Sector",
                          AVG("MonthPercentageChange") AS avg_1m_change,
                          COUNT(*) AS stock_count
                   FROM fundamentals
                   WHERE "Sector" IS NOT NULL
                   GROUP BY "Sector"
                   ORDER BY avg_1m_change DESC"""
            ).fetchdf()
            return {
                "sectors": [
                    {
                        "sector": str(r["Sector"]),
                        "avg_1m_change": _safe(r["avg_1m_change"]),
                        "stock_count": int(r["stock_count"]),
                    }
                    for _, r in df.iterrows()
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

    return {"error": f"Unknown tool: {name}"}


# ── Event logger (fire-and-forget, safe inside SSE generators) ───────────────

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



# Auth event logging removed — Supabase handles auth audit logging natively.


# ── Startup / shutdown ────────────────────────────────────────────────────────

_guard: Optional[GuardModel] = None
_filter: Optional[Filter] = None
_conn: Optional[duckdb.DuckDBPyConnection] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _guard, _filter, _conn, _chat_client
    _guard = GuardModel()
    _filter = Filter()
    _conn = duckdb.connect()
    _chat_client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API"),
    )
    _conn.execute(
        "CREATE VIEW fundamentals AS SELECT * FROM read_parquet('data/fundamentals.parquet');"
    )
    _conn.execute(
        "CREATE VIEW prices AS SELECT * FROM read_parquet('data/consolidated/**/*.parquet', hive_partitioning=1);"
    )
    yield
    if _conn:
        _conn.close()


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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe(val: Any) -> Any:
    """Make a value JSON-safe (replace NaN/Inf with None, unwrap numpy scalars)."""
    # Unwrap numpy scalars (np.float64, np.int64, etc.) to Python native types
    if hasattr(val, "item"):
        val = val.item()
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    # pandas Timestamp → ISO string
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return val


def _df_to_dict(df: pd.DataFrame) -> dict:
    columns = list(df.columns)
    rows = [[_safe(v) for v in row] for row in df.itertuples(index=False)]
    return {"columns": columns, "rows": rows, "row_count": len(df)}


# ── Auth helpers (Supabase built-in auth) ────────────────────────────────────


async def get_current_user(authorization: str = Header(None)) -> dict:
    """FastAPI dependency — verifies the Supabase access token via GoTrue API."""
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
        return {"id": user["id"], "email": user.get("email")}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Pydantic models ───────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    prompt: str


class SaveQueryRequest(BaseModel):
    name: str
    prompt: str
    sql: Optional[str] = None


class HistoryRequest(BaseModel):
    prompt: str
    sql: Optional[str] = None
    row_count: Optional[int] = None


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


# ── Core endpoints ────────────────────────────────────────────────────────────


@app.post("/api/query")
async def run_query(req: QueryRequest):
    if _guard is None or _filter is None or _conn is None:
        raise HTTPException(status_code=503, detail="Models not loaded yet")

    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Empty prompt")

    # Step 1: Safety check
    label, categories = _guard(prompt)
    if label is not SafetyLabel.Safe:
        return {"error": "unsafe", "categories": list(categories)}

    # Step 2: Generate SQL
    sql_query = _filter(prompt)
    if sql_query.error:
        return {"error": sql_query.error}

    # Step 3: Execute
    try:
        df = _conn.execute(
            sql_query.sql_template, list(sql_query.parameters.values())
        ).fetchdf()
        result = _df_to_dict(df)
        result["sql"] = sql_query.sql_template
        return result
    except Exception as exc:
        return {"error": "sql_error", "detail": str(exc)}


@app.post("/api/query/stream")
async def run_query_stream(req: QueryRequest):
    """SSE endpoint — streams pipeline steps as they complete."""
    if _guard is None or _filter is None or _conn is None:
        async def _unavailable():
            yield f"data: {_json.dumps({'error': 'server_error'})}\n\n"
        return StreamingResponse(_unavailable(), media_type="text/event-stream")

    prompt = req.prompt.strip()
    if not prompt:
        async def _empty():
            yield f"data: {_json.dumps({'error': 'empty'})}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    async def generate():
        loop = asyncio.get_event_loop()

        # ── Step 1: safety check ──────────────────────────────────────────────
        yield f"data: {_json.dumps({'step': 'safety'})}\n\n"
        label, categories = await loop.run_in_executor(None, lambda: _guard(prompt))
        if label is not SafetyLabel.Safe:
            yield f"data: {_json.dumps({'error': 'unsafe', 'categories': list(categories)})}\n\n"
            return

        # ── Step 2: LLM → SQL (the slow part) ────────────────────────────────
        yield f"data: {_json.dumps({'step': 'generating'})}\n\n"
        sql_query = await loop.run_in_executor(None, lambda: _filter(prompt))
        if sql_query.error:
            yield f"data: {_json.dumps({'error': sql_query.error})}\n\n"
            return

        # ── Step 3: execute SQL — send the SQL immediately so UI can show it ─
        yield f"data: {_json.dumps({'step': 'executing', 'sql': sql_query.sql_template})}\n\n"
        try:
            df = _conn.execute(
                sql_query.sql_template, list(sql_query.parameters.values())
            ).fetchdf()
            result = _df_to_dict(df)
            result["sql"] = sql_query.sql_template
            result["step"] = "done"
            yield f"data: {_json.dumps(result)}\n\n"
            asyncio.create_task(_log_event(
                "query_run",
                metadata={"prompt": prompt, "row_count": result["row_count"], "sql": sql_query.sql_template},
            ))
        except Exception as exc:
            yield f"data: {_json.dumps({'error': 'sql_error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/stock/{ticker}")
async def get_stock(ticker: str):
    if _conn is None:
        raise HTTPException(status_code=503, detail="DB not ready")

    ticker = ticker.upper()
    try:
        fund_df = _conn.execute(
            'SELECT * FROM fundamentals WHERE "Ticker" = ? LIMIT 1', [ticker]
        ).fetchdf()

        if fund_df.empty:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")

        fund_row = {k: _safe(v) for k, v in fund_df.iloc[0].to_dict().items()}

        try:
            # Aggregate minute-level data to daily OHLCV — keeps response small
            price_df = _conn.execute(
                """SELECT
                       "Datetime"::DATE AS "Datetime",
                       arg_min("Open",  "Datetime") AS "Open",
                       MAX("High")                  AS "High",
                       MIN("Low")                   AS "Low",
                       arg_max("Close", "Datetime") AS "Close",
                       SUM("ShareVolume")            AS "ShareVolume"
                   FROM prices
                   WHERE "Ticker" = ?
                   GROUP BY "Datetime"::DATE
                   ORDER BY "Datetime"::DATE
                   LIMIT 365""",
                [ticker],
            ).fetchdf()
            prices = [{k: _safe(v) for k, v in row.items()} for _, row in price_df.iterrows()]
        except Exception:
            prices = []

        asyncio.create_task(_log_event("stock_viewed", metadata={"ticker": ticker}))
        return {"fundamentals": fund_row, "prices": prices}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/sectors")
async def get_sectors():
    if _conn is None:
        raise HTTPException(status_code=503, detail="DB not ready")
    try:
        df = _conn.execute(
            """SELECT DISTINCT "Ticker", "Sector", "Industry", "MarketCap", "MonthPercentageChange"
               FROM fundamentals
               WHERE "Sector" IS NOT NULL AND "Industry" IS NOT NULL"""
        ).fetchdf()
        dot = _generate_dot(df)
        # Also return sector summary for bar chart
        sector_df = (
            df.groupby("Sector")["MonthPercentageChange"]
            .mean()
            .reset_index()
            .rename(columns={"MonthPercentageChange": "avgChange"})
        )
        sectors = [
            {"sector": str(r["Sector"]), "avgChange": _safe(r["avgChange"])}
            for _, r in sector_df.iterrows()
        ]
        tickers_list = [
            {
                "ticker": str(r["Ticker"]),
                "sector": str(r["Sector"]),
                "industry": str(r["Industry"]),
                "marketCap": _safe(r["MarketCap"]),
                "monthChange": _safe(r["MonthPercentageChange"]),
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

    # Sector nodes
    for sector in df["Sector"].dropna().unique():
        lines.append(
            f'  "{sector}" [fillcolor="#374151", fontcolor="white", fontsize="12", penwidth="0"];'
        )

    for _, row in df.iterrows():
        sector = str(row.get("Sector") or "Unknown")
        industry = str(row.get("Industry") or "Unknown")
        ticker = str(row.get("Ticker") or "")
        market_cap = float(row.get("MarketCap") or 0)
        month_change = float(row.get("MonthPercentageChange") or 0)

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


@app.get("/api/columns")
async def get_columns():
    from models.prompts import columns

    return {"columns": columns}


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
        {"id": q.id, "name": q.name, "prompt": q.prompt, "sql": q.sql, "created_at": q.created_at.isoformat()}
        for q in rows.all()
    ]


@app.post("/api/saved")
async def save_query(req: SaveQueryRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    entry = SavedQuery(user_id=current_user["id"], name=req.name, prompt=req.prompt, sql=req.sql)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {"id": entry.id, "name": entry.name, "prompt": entry.prompt, "sql": entry.sql, "created_at": entry.created_at.isoformat()}


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
            i = start + 7          # len("<think>")
        else:
            end = text.find("</think>", i)
            if end == -1:
                break              # still inside thinking block
            state["in_think"] = False
            i = end + 8            # len("</think>")
    return "".join(out)


TOOL_STATUS_LABELS = {
    "screen_stocks":              "Screening stocks…",
    "get_stock_info":             "Looking up stock data…",
    "get_sector_performance":     "Fetching sector data…",
    "get_live_indian_stock":      "Fetching live Indian stock data…",
    "get_trending_indian_stocks": "Loading trending Indian stocks…",
    "get_indian_stock_news":      "Pulling latest market news…",
    "get_indian_ipo":             "Fetching IPO listings…",
}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    if _chat_client is None or _filter is None or _conn is None:
        raise HTTPException(status_code=503, detail="Backend not ready")

    session_id = req.session_id or str(uuid.uuid4())
    db_session = await db.get(ChatSession, session_id)
    history    = db_session.messages if db_session else []
    loop       = asyncio.get_event_loop()

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

    async def generate():
        messages = (
            [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
            + history
            + [{"role": "user", "content": req.message}]
        )

        # ── Tool-call loop (non-streaming, up to 3 rounds) ────────────────────
        tool_rounds = 0
        while tool_rounds < 3:
            tool_resp = await _chat_client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                tools=CHAT_TOOLS,
                tool_choice="auto",
                max_tokens=1024,
            )
            asst = tool_resp.choices[0].message

            if not asst.tool_calls:
                # No tools needed — model already has an answer; stream it word-by-word
                content = (asst.content or "").strip()
                think_state = {"in_think": False}
                content = _filter_think_tokens(content, think_state)
                words = content.split(" ")
                for i, word in enumerate(words):
                    chunk = word + ("" if i == len(words) - 1 else " ")
                    yield f"data: {_json.dumps({'type': 'token', 'text': chunk})}\n\n"
                    await asyncio.sleep(0.018)   # ~55 wpm typewriter effect

                # Save turn
                updated = history + [
                    {"role": "user",      "content": req.message},
                    {"role": "assistant", "content": content},
                ]
                asyncio.create_task(_save_session(updated[-40:]))
                yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id})}\n\n"
                return

            # Append assistant tool-call message
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
                    for tc in asst.tool_calls
                ],
            })

            # Execute each tool
            for tc in asst.tool_calls:
                t_name = tc.function.name
                t_args = _json.loads(tc.function.arguments or "{}")
                label  = TOOL_STATUS_LABELS.get(t_name, "Working…")
                yield f"data: {_json.dumps({'type': 'tool_call', 'label': label})}\n\n"

                result = await loop.run_in_executor(
                    None, lambda n=t_name, a=t_args: _execute_chat_tool(n, a)
                )
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      _json.dumps(result),
                })

            tool_rounds += 1

        # ── Streaming final answer ────────────────────────────────────────────
        stream = await _chat_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            stream=True,
            max_tokens=2048,
        )

        full_response = ""
        think_state   = {"in_think": False}

        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if not delta:
                continue
            clean = _filter_think_tokens(delta, think_state)
            if clean:
                full_response += clean
                yield f"data: {_json.dumps({'type': 'token', 'text': clean})}\n\n"

        # Save turn
        updated = history + [
            {"role": "user",      "content": req.message},
            {"role": "assistant", "content": full_response},
        ]
        asyncio.create_task(_save_session(updated[-40:]))
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
