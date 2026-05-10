# Backend Flow

End-to-end map of the Stock Screener backend: where data lives, how requests move through the system, and what the LLM does at each step.

---

## 1. High-level architecture

```
┌────────────────────┐
│  Frontend (Next)   │
└─────────┬──────────┘
          │ HTTPS / SSE
          ▼
┌────────────────────────────────────────────────────────────────┐
│ FastAPI app  (api.py)                                          │
│  ── /api/query        (NL → SQL → DuckDB)                      │
│  ── /api/query/stream (same, SSE)                              │
│  ── /api/query/structured (visual builder, no LLM)             │
│  ── /api/chat         (multi-tool agent, SSE)                  │
│  ── /api/stock/{tkr}  (fundamentals + tech indicators)         │
│  ── /api/sectors      (Graphviz DOT for sector tree)           │
│  ── /api/live/*       (Indian Stock API passthrough)           │
│  ── /api/saved, /api/history (per-user persistence)            │
│  ── /api/auth/me      (Supabase JWT verification)              │
└─────────┬────────────────────┬─────────────────┬───────────────┘
          │                    │                 │
   ┌──────▼─────┐       ┌──────▼──────┐   ┌──────▼──────────┐
   │ Models     │       │ DuckDB      │   │ Supabase PG     │
   │ (in-proc)  │       │ (in-mem)    │   │ (async SQLAl.)  │
   │ ── Guard   │       │  views over │   │  saved_queries  │
   │ ── Filter  │       │  Parquet:   │   │  query_history  │
   │ ── SQLVal. │       │  fundament. │   │  chat_sessions  │
   └──────┬─────┘       │  prices/**  │   │  user_events    │
          │             └─────────────┘   └─────────────────┘
   ┌──────▼─────────────┐
   │ External LLM APIs  │
   │ NVIDIA NIM (pri.)  │
   │ OpenRouter (fall)  │
   └────────────────────┘

   ┌───────────────────────────────────────┐
   │ External data services                │
   │ ── indianapi.in   (live BSE/NSE)      │
   │ ── tavily.com     (web search)        │
   └───────────────────────────────────────┘
```

Two entrypoints exist:
- **[api.py](api.py)** — FastAPI server (production).
- **[main.py](main.py)** — Rich-CLI REPL that runs the same pipeline locally (dev/debugging).

---

## 2. Where the data comes from

### 2.1 Stock data — Parquet on disk (read-only)

The screener does not query a live market data API for screening. All quantitative data is in local Parquet files generated synthetically.

| File / glob | Source | What it holds |
|---|---|---|
| `data/fundamentals.parquet` | [scripts/synthetic.py:65](scripts/synthetic.py#L65) `generate_fundamentals` | One row per ticker: Sector, Industry, MarketCap, PeRatio, PbRatio, DividendYield, Beta, FloatShares, EarningsPerShare, Month/YearPercentageChange |
| `data/consolidated/symbol=<TKR>/year=<YYYY>/month=<MM>.parquet` | [scripts/synthetic.py:153](scripts/synthetic.py#L153) `generate_ticker_monthly_data` | Minute-level OHLCV for each ticker, hive-partitioned by symbol + year. Generated via geometric Brownian motion seeded by sector volatility × beta. |

Writes use `compression="zstd"`, `use_dictionary=True`, `write_statistics=True` to enable Parquet predicate pushdown when DuckDB scans the partitions.

### 2.2 DuckDB views (the screening surface)

On startup ([api.py:339-344](api.py#L339-L344)) FastAPI creates an in-memory DuckDB connection and registers two views:

```sql
CREATE VIEW fundamentals AS
    SELECT * FROM read_parquet('data/fundamentals.parquet');

CREATE VIEW prices AS
    SELECT * FROM read_parquet('data/consolidated/**/*.parquet',
                               hive_partitioning=1);
```

The LLM never sees Parquet — it only knows the two view names and the column list.

### 2.3 User state — Supabase Postgres (async SQLAlchemy)

Schema in [scripts/schema.sql](scripts/schema.sql), ORM in [db/models.py](db/models.py). Tables:

| Table | Purpose |
|---|---|
| `saved_queries` | User's saved screens (prompt + sql + filters JSONB + query_type) |
| `query_history` | Per-user run log |
| `chat_sessions` | `messages` JSONB column — the entire conversation per session |
| `user_events` | Append-only event log (`query_run`, `stock_viewed`, etc.) |

Connection wired in [db/session.py](db/session.py) — `DATABASE_URL` from env, rewritten to `postgresql+asyncpg://`, pooled with `pool_pre_ping=True`.

Supabase **Auth** (not the DB) verifies user JWTs. [api.py:389-410](api.py#L389-L410) `get_current_user` calls `GET {SUPABASE_URL}/auth/v1/user` with the bearer token and returns `{id, email}`.

### 2.4 External live data

- **[services/indian_api.py](services/indian_api.py)** — `httpx.Client` against `https://stock.indianapi.in` (`/stock`, `/trending`, `/historical_data`, `/news`, `/ipo`, `/stock_forecasts`). Auth via `INDIAN_API_KEY` env.
- **[services/tavily_search.py](services/tavily_search.py)** — POST to `https://api.tavily.com/search` with `search_depth=advanced`, returns top-N snippets capped at 500 chars each.

---

## 3. How a user query flows: `/api/query` (and `/api/query/stream`)

The screening pipeline is the **core flow**. Steps below correspond to [api.py:463-491](api.py#L463-L491) (sync) and [api.py:494-550](api.py#L494-L550) (SSE).

```
 User prompt ("market cap over 1B and PE under 20")
        │
        ▼
┌───────────────────────────────────────────────┐
│ Step 1: Guard (safety)                        │
│  GuardModel(prompt) — models/guard.py         │
│   ── injection-pattern regex                  │
│   ── Qwen3Guard-Gen-0.6B (local HF model)     │
│   ── returns (SafetyLabel, [categories])      │
└───────┬───────────────────────────────────────┘
        │ Safe?  ── No → return {error: "unsafe", categories: [...]}
        ▼
┌───────────────────────────────────────────────┐
│ Step 2: Filter (NL → SQL)                     │
│  Filter(prompt) — models/filter.py            │
│   ── builds [system, user] messages           │
│   ── system_prompt from models/prompts.py     │
│   ── calls NVIDIA NIM qwen3.5-397b-a17b       │
│      (or OpenRouter if NIM key missing)       │
│   ── temperature=0, max_tokens=4096           │
│   ── expects JSON:                            │
│      {sql_template, parameters, error}        │
└───────┬───────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────┐
│ Step 3: Validate                              │
│  SQLValidator — models/sql_validator.py       │
│   ── JSON shape check                         │
│   ── forbidden keywords (DROP/INSERT/...)     │
│   ── must start with SELECT                   │
│   ── must reference fundamentals|prices       │
│   ── columns ⊆ models/prompts.py:columns      │
│   ── ≤ 2 single-quotes (no string injection)  │
│   ── no `--`, `/*`, `;`                       │
│   ── $1,$2,… params present + numeric         │
└───────┬───────────────────────────────────────┘
        │ error → return {error: <reason>}
        ▼
┌───────────────────────────────────────────────┐
│ Step 4: Execute on DuckDB                     │
│  conn.execute(sql_template, [params...])      │
│   ── runs against fundamentals/prices view    │
│   ── .fetchdf() → pandas DataFrame            │
└───────┬───────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────┐
│ Step 5: Serialize                             │
│  _df_to_dict + _safe                          │
│   ── numpy scalars → Python                   │
│   ── NaN/Inf → None                           │
│   ── Timestamps → ISO strings                 │
│   ── returns {columns, rows, row_count, sql}  │
└───────┬───────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────┐
│ Side effect: log event (fire-and-forget)      │
│  _log_event("query_run", metadata={...})      │
│  → user_events row in Supabase                │
└───────────────────────────────────────────────┘
```

### 3.1 SSE variant — `/api/query/stream`

Same pipeline, but each step yields a server-sent event so the UI can show progress. The slow step (LLM call) runs in `loop.run_in_executor` so the event loop stays responsive.

```
data: {"step": "safety"}
data: {"step": "generating"}
data: {"step": "executing", "sql": "SELECT ..."}
data: {"step": "done", "columns": [...], "rows": [[...], ...], ...}
```

### 3.2 The system prompt — what the LLM is told

[models/prompts.py:64-195](models/prompts.py#L64-L195). It contains:

- The output JSON shape (`sql_template`, `parameters`, `error`).
- The **exact** column whitelist (`columns` list, also enforced in the validator).
- DuckDB-specific syntax requirement.
- Parameter convention (`$1`, `$2`, never inline literals).
- 8 worked few-shot examples.
- A **table-selection rule**: queries that mention time windows / momentum / OHLCV → `prices`; pure ratios → `fundamentals`.
- Error tokens: `"non-finance"`, `"insufficient metrics"`, `"ambiguous"`.

Temperature is `0` so output is deterministic across runs.

---

## 4. Structured (no-LLM) query — `/api/query/structured`

For the visual screener builder. Skips the LLM entirely — the request already contains a list of `{column, operator, value}` filters. [api.py:769-834](api.py#L769-L834):

- Columns checked against `models.prompts.columns` (same whitelist used by the validator).
- Operators mapped through `_OP_MAP = {gt, lt, gte, lte, eq, neq}`.
- Categorical columns only allow `eq` / `neq`; numeric values are `float()`-coerced.
- WHERE clauses built positionally (`$1`, `$2`, …) and passed as DuckDB params — no string interpolation of values.
- Optional `ORDER BY` (column also whitelisted) and `LIMIT` (capped at 500).

Same DuckDB execution and same `_df_to_dict` serialization as the LLM path.

---

## 5. Chat agent flow — `/api/chat`

The chat endpoint is a tool-use loop on top of the same NVIDIA NIM model. [api.py:1041-1197](api.py#L1041-L1197).

```
POST /api/chat {message, session_id?}
        │
        ▼
┌───────────────────────────────────────────────┐
│ Load history                                  │
│  ChatSession.messages from Supabase JSONB     │
│  (graceful no-op if DB unreachable)           │
└───────┬───────────────────────────────────────┘
        ▼
┌───────────────────────────────────────────────┐
│ Build context window                          │
│  [system_prompt] + last 40 msgs (each ≤8000   │
│  chars, truncated with "...(truncated)") +    │
│  new user message                             │
└───────┬───────────────────────────────────────┘
        ▼
┌───────────────────────────────────────────────┐
│ Tool loop (≤5 rounds)                         │
│   ── chat.completions.create(tools=CHAT_TOOLS,│
│        tool_choice="auto", max_tokens=2048)   │
│   ── if no tool_calls → break to final stream │
│   ── else: execute tools, append "tool" msgs  │
│   ── tool result JSON capped at 12000 chars   │
└───────┬───────────────────────────────────────┘
        ▼
┌───────────────────────────────────────────────┐
│ Stream final answer                           │
│  stream=True, max_tokens=4096                 │
│  filter <think>…</think> blocks across chunks │
│  yield SSE {type: "token", text: <delta>}     │
└───────┬───────────────────────────────────────┘
        ▼
┌───────────────────────────────────────────────┐
│ Persist session                               │
│  upsert ChatSession.messages (last 40)        │
│  yield {type: "done", session_id}             │
└───────────────────────────────────────────────┘
```

### 5.1 The 8 tools

Defined in `CHAT_TOOLS` ([api.py:67-177](api.py#L67-L177)) and dispatched by `_execute_chat_tool` ([api.py:180-286](api.py#L180-L286)):

| Tool | Backed by |
|---|---|
| `screen_stocks(query)` | Calls `_filter(query)` — reuses the full NL→SQL→DuckDB pipeline, caps to 15 rows |
| `get_stock_info(ticker)` | DuckDB: `SELECT * FROM fundamentals WHERE Ticker = ?` |
| `get_sector_performance()` | DuckDB aggregate: `AVG(MonthPercentageChange) GROUP BY Sector` |
| `get_live_indian_stock(name)` | `services.indian_api.get_stock` |
| `get_trending_indian_stocks()` | `services.indian_api.get_trending` |
| `get_indian_stock_news(name)` | `services.indian_api.get_news` |
| `get_indian_ipo()` | `services.indian_api.get_ipo` |
| `search_web(query)` | `services.tavily_search.search_web` |

The agent prompt (`CHAT_SYSTEM_PROMPT`, [api.py:43-65](api.py#L43-L65)) constrains scope to finance, demands structured analytical output, and forbids buy/sell recommendations.

### 5.2 `<think>` token stripping

The Qwen 3.5 model emits chain-of-thought wrapped in `<think>…</think>`. `_filter_think_tokens` ([api.py:1007-1026](api.py#L1007-L1026)) tracks an `in_think` state across streaming chunks and strips them so only the final answer reaches the client.

---

## 6. Detail endpoints

### 6.1 `/api/stock/{ticker}` — [api.py:553-646](api.py#L553-L646)

1. Fetch one row from `fundamentals`.
2. Aggregate `prices` (minute-level) → daily OHLCV via `arg_min`/`arg_max`/`MAX`/`MIN`/`SUM`, last 365 days.
3. Compute in pandas: 52-week high/low, SMA(20/50/200), avg vol(20d), 1d/1w/1m/3m/6m/1y % change, 20d annualized vol, RSI(14).
4. Returns `{fundamentals, prices, technicals}`. Logs `stock_viewed`.

### 6.2 `/api/sectors` — [api.py:649-683](api.py#L649-L683)

DuckDB query → pandas groupby for sector averages, plus `_generate_dot` builds a Graphviz DOT string (sector → industry → ticker, color = monthly change sign, font size = log10(market cap)). Frontend renders the DOT.

### 6.3 `/api/columns`, `/api/columns/meta` — [api.py:732-766](api.py#L732-L766)

Expose the column whitelist and per-column type (numeric / categorical) for the visual builder. Categorical columns return their distinct value set straight from DuckDB.

### 6.4 `/api/live/*` — [api.py:840-882](api.py#L840-L882)

Thin passthroughs to `services/indian_api.py` for stock / trending / historical / news / ipo.

### 6.5 `/api/saved`, `/api/history` — [api.py:893-1001](api.py#L893-L1001)

CRUD against Supabase, gated by `Depends(get_current_user)`. The save endpoint has a fallback path that retries without the newer `filters`/`query_type` columns to support DBs that haven't run the migration yet.

---

## 7. Lifecycle

[api.py:322-347](api.py#L322-L347) `lifespan` (FastAPI startup hook):

1. Instantiate `GuardModel()` — downloads/loads `Qwen/Qwen3Guard-Gen-0.6B` to GPU/CPU via Hugging Face.
2. Instantiate `Filter()` — initializes the NIM/OpenRouter client (no network call yet).
3. Open in-memory DuckDB connection, register `fundamentals` and `prices` views over Parquet.
4. Initialize the async OpenAI client used by `/api/chat`. Prefers NIM (`https://integrate.api.nvidia.com/v1`) when `NVIDIA_NIM_API_KEY` is set, falls back to OpenRouter (`https://openrouter.ai/api/v1`).
5. On shutdown, close the DuckDB connection.

CORS allows `FRONTEND_URL` (default `http://localhost:3000`).

---

## 8. Safety layers (defense in depth)

| Layer | Where | Catches |
|---|---|---|
| Injection regex | `models/guard.py:9` `injection_patterns` | crude SQL keywords in raw prompt |
| Guard LLM | `models/guard.py:42` `GuardModel` | unsafe categories (Violent, PII, Jailbreak, …) |
| LLM contract | `models/prompts.py` system prompt | forces parameterized output, no inline literals |
| `temperature=0` | `models/filter.py:43` | reduces drift / hallucinated columns |
| SQL validator | `models/sql_validator.py` | forbidden keywords, column whitelist, comment/multistmt block, parameter integrity |
| DuckDB binding | `conn.execute(sql, params)` | values pass as bound params, never interpolated |
| Read-only views | DuckDB views over Parquet | even a successful injection has nothing to write to |

---

## 9. Observability

- Every screening run writes a `user_events` row with `event_type="query_run"` and `metadata={prompt, row_count, sql}`.
- Stock detail views log `stock_viewed`. Structured queries log `structured_query`.
- Logging uses fire-and-forget `asyncio.create_task` and swallows DB errors so the user request is never blocked by telemetry failure.

---

## 10. CLI counterpart — `main.py`

A standalone Rich-based REPL. Same `Guard → Filter → SQLValidator → DuckDB` pipeline as the HTTP `/api/query` endpoint, just printed to a terminal table. Useful for testing prompts without spinning up the API.

```
$ uv run main.py
✓ All systems ready!
user: market cap over 1 billion and PE under 20
SQL: SELECT * FROM fundamentals WHERE "MarketCap" > $1 AND "PeRatio" < $2
Parameters: {'$1': 1000000000, '$2': 20}
✓ Query returned N rows
[rich table…]
```
