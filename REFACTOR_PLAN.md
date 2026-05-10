# Refactor Plan — MVP

College-project scope. Realistic timeline: **2–3 weekends of focused work**. Goal: turn the current backend (fake data, jargon-driven, returns matches) into a backend that satisfies the mission — *a novice user finds the best stock by typing in plain English*.

---

## Mission, restated

> A user who has never traded should type "safe stocks for retirement" or "growing tech companies in India" and get a small list of **real** stocks ranked for them, with one-line explanations of why each fits.

---

## Current vs target — at a glance

| Aspect | Today | After this plan |
|---|---|---|
| Data | Synthetic Parquet, 100 fake tickers | Real data from yfinance, ~300–500 tickers (S&P 500 + Nifty 50) |
| Storage | Local Parquet + DuckDB views | Supabase Postgres (single source of truth) |
| Granularity | Minute-level OHLCV | **Daily only** (drop minute data — beginners don't need it) |
| Schema | 12 jargon columns | Same + 6 novice-friendly columns (CompanyName, Country, RevenueGrowth, DebtToEquity, ProfitMargin, Description) |
| LLM SQL flavor | DuckDB | PostgreSQL |
| Output | Filtered rows in arbitrary order | Ranked top-N + per-row "why this fits" sentence |
| Vague queries ("safe stocks") | `error: "ambiguous"` | Translated to concrete filters via intent map |
| Front door | Screener UI | Chat (with screener as power-user mode) |
| Deploy | Needs persistent disk for Parquet | Just `DATABASE_URL` — works on any host |

---

## What stays unchanged

- [models/guard.py](models/guard.py) — safety classifier
- [models/sql_validator.py](models/sql_validator.py) — already syntax-agnostic, picks up new columns from the registry
- [api.py](api.py) endpoint shapes — `/api/query`, `/api/chat`, `/api/stock/{ticker}` keep their request/response contracts
- [db/](db/) layer — async SQLAlchemy, just gets new tables added
- Supabase Auth flow ([api.py:389-410](api.py#L389-L410))
- [services/indian_api.py](services/indian_api.py) and [services/tavily_search.py](services/tavily_search.py)
- Frontend response contract (rows + columns + sql)

---

## LLM stack (locked)

Two models on **NVIDIA NIM** — both free, no new vendor keys, OpenRouter remains as automatic fallback if NIM key is unset.

| Workload | Model | Reason |
|---|---|---|
| **Filter (NL → SQL)** — Phase 3 | `meta/llama-3.3-70b-instruct` | No `<think>` tokens (Qwen 3.5's reasoning blocks break JSON parsing on the filter path), 5–10× faster than 397B-MoE, supports `response_format={"type":"json_object"}`. |
| **Per-row narrative** — Phase 5.2 | `meta/llama-3.3-70b-instruct` | Same client, same call shape — reuse. |
| **Novice intent fallback** *(stretch)* | `meta/llama-3.1-8b-instruct` | Sub-200ms, only fires when the keyword matcher misses. |
| **Chat agent** — unchanged | `qwen/qwen3.5-397b-a17b` | Reasoning + 8-tool routing + analytical narrative — this is where the big model earns its keep. `<think>` stripping already implemented at [api.py:1007-1026](api.py#L1007-L1026). |
| **Guard (safety)** — unchanged | `Qwen/Qwen3Guard-Gen-0.6B` (local HF) | Already wired, runs in-process. |

**Total code change:** two strings.

```python
# models/filter.py:18
def __init__(self, model: str = "meta/llama-3.3-70b-instruct") -> None:

# api.py:41 — unchanged
CHAT_MODEL = "qwen/qwen3.5-397b-a17b"
```

**Backup if NIM rate-limits during the demo:** OpenRouter has free Llama 3.3 70B on the same `meta/llama-3.3-70b-instruct` slug. The existing fallback in [models/filter.py:21-31](models/filter.py#L21-L31) and [api.py:329-338](api.py#L329-L338) routes there automatically when `NVIDIA_NIM_API_KEY` is unset. No code change.

**Optional upgrade path (skip for MVP):** Anthropic Haiku 4.5 for Filter/narrative + Sonnet 4.6 for chat is the highest-quality config but costs ~$5–15 over the project's life and adds an `ANTHROPIC_API_KEY` env var. Note as a stretch only.

---

## Phase 0 — Decisions (½ day)

Lock these before writing code:

- [ ] **Drop minute-level OHLCV.** No real source provides it cheaply, screener doesn't need it. `data/consolidated/**/*.parquet` and the `prices` view go away.
- [ ] **Drop DuckDB.** All queries run via Postgres through the existing async SQLAlchemy engine.
- [ ] **Initial ticker universe:** S&P 500 + Nifty 50 ≈ 550 stocks. Stored as a static list `scripts/tickers.py`.
- [ ] **Refresh cadence:** nightly batch (run manually for the project; can be wired to a GitHub Action cron later).

---

## Phase 1 — Schema migration to Supabase (1 day)

### 1.1 New Supabase tables

Add to [scripts/schema.sql](scripts/schema.sql):

```sql
-- Stock universe + slow-changing fundamentals
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker          TEXT        PRIMARY KEY,
    company_name    TEXT        NOT NULL,
    country         TEXT        NOT NULL,         -- 'US' | 'IN'
    sector          TEXT,
    industry        TEXT,
    description     TEXT,
    market_cap      BIGINT,
    pe_ratio        NUMERIC(10,2),
    pb_ratio        NUMERIC(10,2),
    dividend_yield  NUMERIC(10,4),
    beta            NUMERIC(6,3),
    eps             NUMERIC(10,2),
    revenue_growth  NUMERIC(10,4),
    profit_margin   NUMERIC(10,4),
    debt_to_equity  NUMERIC(10,2),
    return_on_equity NUMERIC(10,4),
    week52_high     NUMERIC(12,2),
    week52_low      NUMERIC(12,2),
    last_price      NUMERIC(12,2),
    month_change    NUMERIC(10,4),
    year_change     NUMERIC(10,4),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_fundamentals_sector       ON fundamentals(sector);
CREATE INDEX idx_fundamentals_country      ON fundamentals(country);
CREATE INDEX idx_fundamentals_market_cap   ON fundamentals(market_cap DESC);

-- Daily OHLCV (replaces minute-level data)
CREATE TABLE IF NOT EXISTS daily_prices (
    ticker      TEXT        NOT NULL,
    date        DATE        NOT NULL,
    open        NUMERIC(12,2),
    high        NUMERIC(12,2),
    low         NUMERIC(12,2),
    close       NUMERIC(12,2),
    volume      BIGINT,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX idx_daily_prices_ticker_date  ON daily_prices(ticker, date DESC);
```

**Size sanity check (free-tier safe):** 550 tickers × 30 cols ≈ 200 KB. 550 × 252 days × 5 years × ~80 bytes ≈ 55 MB. Total well under the **500 MB Supabase free-tier limit**.

### 1.2 Add SQLAlchemy ORM models

In [db/models.py](db/models.py), add `Fundamental` and `DailyPrice` mapped classes alongside the existing `SavedQuery` / `QueryHistory` / `ChatSession` / `UserEvent`. Use `Mapped[type]` style consistent with current code.

### 1.3 Connection model

The existing `engine` in [db/session.py](db/session.py) already uses asyncpg + connection pooling. Switch `DATABASE_URL` to **Supabase's connection pooler** (port 6543) — supports more concurrent connections than direct (port 5432), important when chat tool loops fan out.

---

## Phase 2 — yfinance ingester (1 day)

Replace [scripts/synthetic.py](scripts/synthetic.py) with `scripts/ingest.py`.

> **On data freshness:** yfinance is a live Yahoo Finance scraper, not a static dataset — it returns current 2026 market data updated daily after market close. Documented backup sources if Yahoo gets blocked: Financial Modeling Prep (250 req/day free), Twelve Data (800 req/day free), Finnhub (60 req/min free). For Indian-only failover, the existing `services/indian_api.py` integration stays available.

### 2.1 Structure

```python
# scripts/ingest.py
import asyncio, time
import yfinance as yf
from sqlalchemy import insert
from db.session import AsyncSessionLocal
from db.models import Fundamental, DailyPrice
from scripts.tickers import US_TICKERS, IN_TICKERS  # new file

async def ingest_fundamentals(): ...
async def ingest_prices(years: int = 2): ...

if __name__ == "__main__":
    asyncio.run(main())
```

### 2.2 Fundamentals via `Ticker.info`

`yf.Ticker(t).info` returns a dict with `marketCap`, `trailingPE`, `priceToBook`, `dividendYield`, `beta`, `trailingEps`, `revenueGrowth`, `profitMargins`, `debtToEquity`, `returnOnEquity`, `fiftyTwoWeekHigh`, `fiftyTwoWeekLow`, `longName`, `longBusinessSummary`, `sector`, `industry`. Map directly to columns.

**Rate-limit handling (per yfinance research):**
- Process in batches of 50 tickers, `time.sleep(2)` between batches.
- Wrap each `Ticker.info` in `try/except` — skip and log on failure rather than abort.
- Use `requests-cache` (1-day TTL) so re-runs during dev don't re-hit Yahoo.
- **Important:** the dedicated `financials` / `balance_sheet` / `cashflow` methods are broken in current yfinance and return empty — stick to `.info`.

**Indian tickers:** append `.NS` (NSE) or `.BO` (BSE), e.g. `RELIANCE.NS`. Map back to the symbol-only `ticker` column when storing.

### 2.3 Daily prices via `yf.download()`

```python
df = yf.download(
    tickers, period="2y", interval="1d",
    group_by="ticker", auto_adjust=True, threads=True,
)
```

Returns a multi-index DataFrame; flatten and bulk-insert into `daily_prices`. `yf.download` is the batch-friendly path — much faster and less rate-limit-prone than per-ticker loops.

### 2.4 Computed fields (don't trust Yahoo's monthly/yearly fields)

After the download, compute from the daily prices we just stored:
- `month_change` = `(close[-1] - close[-21]) / close[-21]`
- `year_change` = `(close[-1] - close[-252]) / close[-252]`
- `last_price` = `close[-1]`

### 2.5 Atomic refresh

```sql
BEGIN;
TRUNCATE fundamentals;
-- bulk insert
COMMIT;
```

For `daily_prices`, use `INSERT ... ON CONFLICT (ticker, date) DO NOTHING` so re-runs are idempotent.

### 2.6 Run

```bash
python -m scripts.ingest          # full refresh, ~10–15 min for 550 tickers
python -m scripts.ingest --quick  # fundamentals only, ~3 min
```

---

## Phase 3 — Make the LLM speak Postgres (½ day)

### 3.1 Update the system prompt

In [models/prompts.py](models/prompts.py):
- Change `"Use DuckDB SQL syntax"` → `"Use PostgreSQL syntax"`.
- Update column whitelist to lowercase snake_case to match the new tables (`market_cap`, not `MarketCap`).
- Drop the `prices` table — only `fundamentals` is exposed to the screener LLM. (Time-series logic moves to the precomputed `month_change` / `year_change` columns.)
- Drop the "stocks up 10% in 10 days" example with the window function — replace with a `month_change > 0.10` example using the precomputed column.

### 3.2 Update `Filter` to use `response_format`

[models/filter.py:39-44](models/filter.py#L39):

```python
completion_obj = self.client.chat.completions.create(
    model=self.model_name,
    messages=messages,
    max_completion_tokens=1024,                  # tightened from 4096
    temperature=0,
    response_format={"type": "json_object"},     # forces valid JSON
    timeout=8,                                   # never hang forever
)
```

### 3.3 Run queries through SQLAlchemy

In [api.py](api.py), replace the DuckDB execution path:

```python
# Before:
df = _conn.execute(sql_template, list(parameters.values())).fetchdf()

# After:
async with AsyncSessionLocal() as db:
    result = await db.execute(text(sql_template), parameters)
    rows = result.mappings().all()
    df = pd.DataFrame(rows)
```

The LLM's `$1`, `$2` parameter style works directly in Postgres — no rewriting needed.

### 3.4 Migrate every `_conn.execute` call site

There are **four** endpoints touching DuckDB today. All need the SQLAlchemy swap from §3.3:

| Endpoint | Current source | Target |
|---|---|---|
| `/api/query`, `/api/query/stream` ([api.py:484](api.py#L484), [api.py:528](api.py#L528)) | `fundamentals` view (Parquet) | `fundamentals` table (Postgres) |
| `/api/query/structured` ([api.py:825](api.py#L825)) | `fundamentals` view | `fundamentals` table — same query, just async session |
| `/api/sectors` ([api.py:654](api.py#L654)) | `fundamentals` view | `fundamentals` table |
| `/api/stock/{ticker}` ([api.py:560](api.py#L560), [api.py:573](api.py#L573)) | `fundamentals` + minute-level `prices` | `fundamentals` + `daily_prices` |
| Chat agent tools ([api.py:209](api.py#L209), [api.py:220](api.py#L220)) — `get_stock_info`, `get_sector_performance` | DuckDB | Postgres via async session |

### 3.5 Rewrite `/api/stock/{ticker}` for daily data

This endpoint loses minute-level OHLCV but **technical indicators stay** — they actually become more correct on daily bars (SMA-20 over 20 trading days, not 20 minutes).

```python
# Replace the arg_min/arg_max/MAX/MIN aggregation (api.py:573-587) with:
price_df = await db.execute(
    text("""SELECT date, open, high, low, close, volume
            FROM daily_prices
            WHERE ticker = :ticker
            ORDER BY date DESC
            LIMIT 365"""),
    {"ticker": ticker},
)
```

The SMA-20/50/200, RSI-14, volatility, and period-change calculations at [api.py:589-636](api.py#L589-L636) all read from `closes` / `volumes` / `highs` / `lows` Series — schema-agnostic. They'll work unchanged once `price_df` is sourced from `daily_prices`.

### 3.6 Switch `Filter` to `AsyncOpenAI`

Today `Filter` ([models/filter.py:5,23](models/filter.py#L23)) uses the sync `OpenAI` client. The non-streaming `/api/query` calls it directly inside an `async def` — that **blocks the entire event loop** during the LLM call. While one user waits, no other request can be served.

```python
# models/filter.py — switch:
from openai import AsyncOpenAI
self.client = AsyncOpenAI(base_url=..., api_key=...)

async def __call__(self, prompt: str) -> SQLQuery:
    completion_obj = await self.client.chat.completions.create(...)
    ...
```

Then in [api.py:478](api.py#L478) and [api.py:520](api.py#L520): `await _filter(prompt)`. The `run_in_executor` workaround in the SSE path goes away.

### 3.7 Drop DuckDB and retire `main.py`

- Remove `duckdb` from [pyproject.toml](pyproject.toml).
- Remove `_conn` global, `lifespan` view creation, all `_conn.execute` references.
- **Retire [main.py](main.py)** — the SSE endpoint covers the same dev-loop need (Postman / curl streams the same pipeline). Keeping a parallel CLI doubles the migration surface for no MVP value.

---

## Phase 4 — The novice translator (1 day) — **mission-critical**

The biggest single change toward the project's purpose.

### 4.1 Module: `models/intent.py`

```python
INTENT_MAP = {
    "safe": {
        "filters": "beta < 1.0 AND market_cap > 10e9 AND dividend_yield > 0.02",
        "ranking": "(market_cap / 1e9) + (dividend_yield * 100) - (beta * 5)",
        "explanation": "low volatility, established companies, steady dividends",
    },
    "growth": {
        "filters": "revenue_growth > 0.10 AND profit_margin > 0",
        "ranking": "revenue_growth * 100 + profit_margin * 50",
        "explanation": "fast revenue growth with positive margins",
    },
    "value": {
        "filters": "pe_ratio < 15 AND pe_ratio > 0 AND pb_ratio < 2",
        "ranking": "(1.0 / NULLIF(pe_ratio, 0)) * 100 + dividend_yield * 50",
        "explanation": "trading below typical valuation multiples",
    },
    "income": {
        "filters": "dividend_yield > 0.03",
        "ranking": "dividend_yield * 100 + LOG(market_cap)",
        "explanation": "high dividend yields from stable payers",
    },
    "blue_chip": {
        "filters": "market_cap > 50e9 AND dividend_yield > 0",
        "ranking": "LOG(market_cap)",
        "explanation": "largest, most established companies",
    },
}
```

### 4.2 Detect intent

```python
def detect_intent(prompt: str) -> str | None:
    """Returns the intent key or None if not novice-style."""
    p = prompt.lower()
    if any(w in p for w in ["safe", "stable", "low risk", "retirement", "conservative"]):
        return "safe"
    if any(w in p for w in ["growth", "growing", "fast-growing", "expanding"]):
        return "growth"
    if any(w in p for w in ["cheap", "undervalued", "value", "bargain"]):
        return "value"
    if any(w in p for w in ["dividend", "income", "yield", "passive"]):
        return "income"
    if any(w in p for w in ["blue chip", "large cap", "established", "big company"]):
        return "blue_chip"
    return None
```

### 4.3 Wire into the query path

In [api.py](api.py) `/api/query`:

```python
intent = detect_intent(prompt)
if intent:
    template = INTENT_MAP[intent]
    country_clause = f" AND country = '{country}'" if country else ""
    sql = f"""
        SELECT *, ({template['ranking']}) AS match_score
        FROM fundamentals
        WHERE {template['filters']}{country_clause}
        ORDER BY match_score DESC
        LIMIT 20
    """
    # Skip the LLM entirely — instant response
else:
    # Fall through to the existing LLM → SQL pipeline
    sql_query = _filter(prompt)
    ...
```

This is a deterministic fast path for the most common novice queries — **zero LLM call, sub-100ms response, no failure modes**.

### 4.4 Country context

Frontend sends `{prompt: "...", country: "US" | "IN" | null}`. Plumb through to `/api/query`.

---

## Phase 5 — Ranking + narrative output (1 day)

### 5.1 Always rank

Even when the LLM generates SQL, append `ORDER BY market_cap DESC LIMIT 20` if the LLM didn't include one. Beginners want a small list, not 500 rows.

In [models/sql_validator.py](models/sql_validator.py): if no `ORDER BY` and no `LIMIT`, append `ORDER BY market_cap DESC LIMIT 20`.

### 5.2 Per-row narrative ("why this fits")

After SQL execution, take top 5 rows and call **`meta/llama-3.3-70b-instruct` on NIM** (same client used by Filter — locked in the LLM stack section above) for one-sentence explanations:

```python
async def explain_results(prompt: str, top_rows: list[dict], intent: str | None) -> dict[str, str]:
    """Returns {ticker: explanation} for the top rows."""
    explanation_prompt = f"""User asked: "{prompt}"
For each stock below, write ONE sentence (max 20 words) explaining why it matches.
{json.dumps(top_rows[:5])}
Output JSON: {{"AAPL": "...", "MSFT": "...", ...}}"""
    # one LLM call, response_format=json_object, ~500–800ms on Llama 3.3 70B
```

Stream as a separate SSE event after rows arrive:
```
data: {"step": "executing", "sql": "..."}
data: {"step": "done", "rows": [...], "row_count": 50}
data: {"step": "explanations", "explanations": {"AAPL": "...", ...}}
```

Frontend: rows appear immediately, the "why" text fades in ~1s later.

### 5.3 Skip narrative on the structured-builder path

`/api/query/structured` is for users who already know what they want — no explanation needed.

---

## Phase 6 — Better failure modes (½ day)

### 6.1 Clarifying-question fallback

When the LLM returns `error: "ambiguous"` or `error: "non-finance"` and the prompt looks vaguely finance-related, return:

```json
{
  "type": "clarify",
  "question": "What matters most to you?",
  "presets": [
    {"label": "Safe & stable", "intent": "safe"},
    {"label": "Fast growth", "intent": "growth"},
    {"label": "Cheap valuations", "intent": "value"},
    {"label": "Dividend income", "intent": "income"}
  ]
}
```

Frontend renders chips → clicking one re-submits with the chosen intent.

### 6.2 Preset library endpoint

```python
@app.get("/api/presets")
async def get_presets():
    return [
        {"id": "safe-us",     "label": "Safe US dividend stocks",   "intent": "safe",   "country": "US"},
        {"id": "growth-tech", "label": "Growing tech companies",    "intent": "growth", "sector": "Technology"},
        {"id": "value-in",    "label": "Cheap Indian stocks",       "intent": "value",  "country": "IN"},
        {"id": "income",      "label": "High dividend income",      "intent": "income"},
        {"id": "blue-chip",   "label": "Blue-chip large caps",      "intent": "blue_chip"},
    ]
```

Static — zero LLM, zero DB query for the home page.

---

## Phase 7 — Make chat the front door (½ day, mostly frontend)

Backend changes are minimal — the chat endpoint already exists. The work:

- Frontend home page: chat input replaces the screener as the primary surface.
- "Advanced screener" link in nav → goes to the existing builder UI.
- Chat agent's `screen_stocks` tool (in `_execute_chat_tool`) → reuse the new intent path so a chat message like "show me safe stocks" hits the deterministic fast path too.

---

## Phase 8 — Deployment cleanup (½ day)

### 8.1 Remove disk dependencies

- Delete `data/` directory entirely (no more Parquet).
- Remove `data/` from any deploy configs.
- The backend now only needs: env vars + Python packages. No volumes.

### 8.2 Hosting

- **Backend:** Render / Railway / Fly.io free tier — any of them work now that there's no persistent disk.
- **Database:** Supabase free tier (already used).
- **Frontend:** Vercel (existing).
- **Ingester:** GitHub Actions cron (`schedule: '0 2 * * *'` for nightly 2am UTC). Or run manually for the demo.

### 8.3 Env vars summary

```
DATABASE_URL=postgresql://...:6543/postgres   # pooler
SUPABASE_URL=https://....supabase.co
SUPABASE_PUBLISHABLE_KEY=...
NVIDIA_NIM_API_KEY=...
INDIAN_API_KEY=...
TAVILY_API_KEY=...
FRONTEND_URL=https://....vercel.app
```

---

## Suggested execution order

| Day | Phase | Deliverable |
|---|---|---|
| 1 (½) | 0 | Decisions locked, plan reviewed |
| 1 | 1 | Supabase schema + ORM models, no data yet |
| 2 | 2 | yfinance ingester running, Supabase populated |
| 3 (½) | 3 | LLM emits Postgres SQL, DuckDB removed, queries work end-to-end |
| 4 | 4 | Novice translator live — `"safe stocks"` returns ranked results |
| 5 | 5 | Ranking + narrative explanations |
| 6 (½) | 6 | Clarifying questions + presets |
| 6 (½) | 7 | Chat as front door (frontend) |
| 7 (½) | 8 | Deployed |

**~7 days of focused work, comfortably within a college-project timeline.**

---

## Explicitly out of scope (skip for MVP)

- Caching (LRU, query cache, Redis) — not needed at this scale.
- User-profile-based personalization (risk tolerance, budget) — Phase 4 country context is enough.
- Real-time websockets / streaming quotes — daily data is fine.
- Multiple data sources / failover — yfinance is the only source.
- Backfilling historical fundamentals (snapshots only — current values).
- News ingestion to DB — keep using Tavily / indianapi.in live.
- ML-based ranking — the deterministic intent formulas are honest and explainable, which is *better* for a novice product.
- Authentication for the screener — public read access; auth still gates `/api/saved` and `/api/history`.
- Admin UI for managing tickers — edit `scripts/tickers.py`.

---

## Stretch goals (only if time permits after MVP)

- Frontend "compare two stocks" view that hits `/api/stock/{ticker}` twice.
- Basic charting on the stock detail page using the new `daily_prices` table.
- Email/Slack alert when a saved query has new matches (use Supabase Edge Functions).
- LLM-driven intent detection for queries the keyword matcher doesn't catch (fall back to a single small-model call).

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| yfinance gets blocked mid-demo | Keep last successful Postgres snapshot as fallback; ingester has retry + sleep |
| LLM emits invalid Postgres SQL | Validator already catches keyword/column issues; `response_format={"type":"json_object"}` makes JSON parse failures impossible |
| Supabase pauses after 1 week idle (free tier) | Hit a `/api/health` endpoint from the frontend on load; or use Supabase paid plan ($25/mo) for graded demos |
| Postgres window queries slow under load | Indexes on `(ticker, date)` cover the common cases; add `statement_timeout = '5s'` as guardrail |
| LLM hallucinates a column | Validator's column whitelist already blocks this — error returns to clarifying-question fallback |

---

## Definition of done

The MVP is shippable when a non-trader teammate, given only the URL, can:

1. Land on the home page and see ~5 preset options.
2. Click "Safe US dividend stocks" → see 20 real US stocks ranked, each with a one-line explanation.
3. Type "growing Indian tech companies" in chat → get a narrative analysis with real Nifty 50 tickers.
4. Click on AAPL → see real fundamentals + a 1-year price chart.
5. None of it requires them to know what P/E, beta, or float shares mean.

That's the mission. The plan above gets you there.

---

## Sources consulted

- yfinance rate-limit + bulk-download patterns: [yfinance Library — Complete Guide (AlgoTrading101)](https://algotrading101.com/learn/yfinance-guide/), [Rate Limiting Best Practices (Sling Academy)](https://www.slingacademy.com/article/rate-limiting-and-api-best-practices-for-yfinance/), [Working with Multiple Tickers (DeepWiki)](https://deepwiki.com/ranaroussi/yfinance/4.2-working-with-multiple-tickers)
- Supabase free-tier limits (500 MB, pooler 200 conns, 1-week idle pause): [Supabase Pricing](https://supabase.com/pricing), [Free Tier Limits 2026 (AI Agency Plus)](https://aiagencyplus.com/supabase-free-tier-limits/)